"""Decompose a commander into their mechanic roles using oracle text patterns.

For each matched pattern, the script checks whether that key is registered in
``synergy/commander_mechanics.py`` as a consumer key, a producer key, or both.
Patterns that fire but have no SQL entry yet are listed as "TODO" — those
represent gaps to fill in commander_mechanics.py.

Consumer  — the commander *needs* the deck full of these cards
Producer  — the commander *outputs* this; deck wants amplifiers

Usage
-----
    docker compose run --rm ingest python scripts/decompose_commanders.py "Tyvar the Bellicose"
    docker compose run --rm ingest python scripts/decompose_commanders.py "Raggadragga"
    docker compose run --rm ingest python scripts/decompose_commanders.py "Atraxa"
    # partial / case-insensitive match:
    docker compose run --rm ingest python scripts/decompose_commanders.py tyvar
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).parent.parent))
from synergy.commander_mechanics import (
    PATTERN_KEY_TO_CONSUMER_SQL,
    PATTERN_KEY_TO_PRODUCER_SQL,
)

DATABASE_URL = (
    os.environ.get("DATABASE_URL", "")
    .replace("postgresql+asyncpg://", "postgresql://")
)

# ── Oracle text detection patterns ───────────────────────────────────────────
# Each entry: (pattern_key, label, compiled_regex)
#
# These patterns detect WHAT a commander does; commander_mechanics.py then
# classifies each detected key as consumer or producer.

ORACLE_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    # ETB trigger
    ("etb_trigger", "ETB trigger",
     re.compile(
         r"when(?:ever)?\s+"
         r"(?:(?:a |an |another |one or more )?(?:creature|permanent|token|land|artifact|enchantment)"
         r".{0,40}|.{2,50}?)"
         r"enters(?:\s+the battlefield)?",
         re.I,
     )),

    # Spell cast — creature
    ("cast_trigger_creature", "Creature cast trigger",
     re.compile(r"when(?:ever)?\s+you cast (?:a |an )?creature", re.I)),

    # Spell cast — instant/sorcery
    ("cast_trigger_instant_sorcery", "Instant/sorcery cast trigger",
     re.compile(r"when(?:ever)?\s+you cast (?:a |an )?(?:instant|sorcery|noncreature)", re.I)),

    # Spell cast — enchantment
    ("cast_trigger_enchantment", "Enchantment cast trigger",
     re.compile(r"when(?:ever)?\s+you cast (?:a |an )?enchantment", re.I)),

    # Spell cast — artifact
    ("cast_trigger_artifact", "Artifact cast trigger",
     re.compile(r"when(?:ever)?\s+you cast (?:a |an )?artifact", re.I)),

    # Spell cast — historic
    ("cast_trigger_historic", "Historic spell cast trigger",
     re.compile(r"when(?:ever)?\s+you cast (?:a |an )?historic", re.I)),

    # Spell cast — color-based
    ("cast_trigger_colored", "Color-based cast trigger",
     re.compile(
         r"when(?:ever)?\s+you cast (?:a |an )?"
         r"(?:red|blue|green|white|black|colorless|multicolored|monocolored)"
         r"(?:\s+or\s+(?:red|blue|green|white|black|colorless|multicolored|artifact|creature))?"
         r"\s+spell",
         re.I,
     )),

    # Group hug
    ("group_hug", "Group hug",
     re.compile(
         r"each player (?:draws?|may draw|may put)"
         r"|each player's draw step.{0,50}draws?"
         r"|\bparley\b",
         re.I,
     )),

    # Poison / infect / toxic
    ("poison_infect", "Poison / infect / toxic",
     re.compile(r"\binfect\b|\bpoison counter|\btoxic\b", re.I)),

    # Equipment matters
    ("equipment_matters", "Equipment matters",
     re.compile(
         r"equipped creature"
         r"|equipment (?:you control|attached|spell|token|are)"
         r"|target equipment"
         r"|aura or equipment"
         r"|aura,?\s+and equipment"
         r"|aura,\s+equipment",
         re.I,
     )),

    # Artifact count
    ("artifact_count", "Artifact count matters",
     re.compile(r"for each (?:tapped )?artifact you control|artifacts you control", re.I)),

    # Artifact creatures
    ("artifact_creatures", "Artifact creatures matter",
     re.compile(r"artifact creatures? you control", re.I)),

    # Death trigger
    ("death_trigger", "Death trigger",
     re.compile(
         r"when(?:ever)?\s+(?:a |an |another |one or more )?(?:nontoken )?creature"
         r".{0,40}dies",
         re.I,
     )),

    # Graveyard from play
    ("graveyard_from_play", "Permanent to graveyard trigger",
     re.compile(
         r"when(?:ever)?\s+(?:a |an )?(?:nontoken )?permanent.{0,40}"
         r"(?:put into|goes to|enters?) (?:a |your )?graveyard",
         re.I,
     )),

    # Attack trigger
    ("attack_trigger", "Attack trigger",
     re.compile(
         r"when(?:ever)?\s+"
         r"(?:this creature|one or more creatures you control|a creature you control|you"
         r"|.{2,50}?)"
         r"\s+attacks?(?:\s+alone)?",
         re.I,
     )),

    # Combat damage to player
    ("combat_damage_to_player", "Combat damage to player",
     re.compile(r"deals? combat damage to (?:a |an )?(?:player|opponent)", re.I)),

    # Madness payoff
    ("madness_payoff", "Madness payoff",
     re.compile(r"\bmadness\b|for its madness cost", re.I)),

    # Discard outlet
    ("discard_outlet", "Discard outlet",
     re.compile(r"discard (?:a |one or more )?(?:card|cards)", re.I)),

    # Sacrifice payoff
    ("sacrifice_payoff", "Sacrifice payoff",
     re.compile(
         r"when(?:ever)?\s+you sacrifice"
         r"|sacrifice (?:a |an |another )?(?:creature|permanent)",
         re.I,
     )),

    # Landfall
    ("landfall", "Landfall",
     re.compile(r"\blandfall\b|when(?:ever)?\s+(?:a |one or more )?land.{0,20}enters", re.I)),

    # Counter placement
    ("counter_placement", "Counter placement",
     re.compile(r"put (?:a |one or more |an? )?\+1/\+1 counter", re.I)),

    # Lifegain trigger
    ("lifegain_trigger", "Life gain trigger",
     re.compile(r"when(?:ever)?\s+you (?:gain|gained) life", re.I)),

    # Draw trigger
    ("draw_trigger", "Draw trigger",
     re.compile(
         r"when(?:ever)?\s+you draw (?:a card|cards|your (?:first|second|third) card)",
         re.I,
     )),

    # Token trigger
    ("token_trigger", "Token creation trigger",
     re.compile(
         r"when(?:ever)?\s+(?:one or more )?tokens? (?:enters?|(?:is |are )?created|(?:is |are )?put)",
         re.I,
     )),

    # Trigger doubling
    ("trigger_doubling", "Trigger doubling",
     re.compile(r"triggers? an additional time|triggers? twice", re.I)),

    # Proliferate
    ("proliferate_matters", "Proliferate",
     re.compile(r"\bproliferate\b", re.I)),

    # Second spell
    ("second_spell", "Second spell matters",
     re.compile(
         r"second spell (?:each turn|you cast this turn)"
         r"|when(?:ever)?\s+you cast your second",
         re.I,
     )),

    # Punisher
    ("punisher", "Punisher effect",
     re.compile(
         r"each opponent (?:loses? \d+ life|takes? \d+ damage)"
         r"|deals? \d+ damage to each opponent",
         re.I,
     )),

    # Weenie matters
    ("weenie_matters", "Weenie matters",
     re.compile(
         r"power (?:of )?(?:1|2|one|two) or less"
         r"|creatures? with power (?:1|2|one|two) or less",
         re.I,
     )),

    # Unearth / encore
    ("unearth_encore", "Unearth / encore / temporary reanimation",
     re.compile(
         r"\bunearth\b|\bencore\b"
         r"|(?:exile|sacrifice) (?:it|them) at the beginning of the next end step",
         re.I,
     )),

    # Graveyard payoff
    ("graveyard_payoff", "Graveyard payoff",
     re.compile(
         r"from (?:your |a |the )?graveyard.{0,30}(?:cast|play|battlefield)"
         r"|when.{0,30}put into (?:a |your )?graveyard from",
         re.I,
     )),

    # Keyword lord
    ("keyword_lord", "Keyword grant (lord)",
     re.compile(
         r"(?:creatures? you control|other [a-z\s]+you control).{0,40}"
         r"(?:gain|have|get) (?:flying|trample|haste|menace|hexproof|lifelink|"
         r"deathtouch|reach|vigilance|indestructible|first strike|double strike)",
         re.I,
     )),

    # Cycling trigger
    ("cycling_trigger", "Cycling trigger",
     re.compile(r"when(?:ever)?\s+(?:a player )?(?:cycles?|discards?) (?:a |this )?card", re.I)),

    # Counter doubler
    ("counter_doubler", "Counter doubler",
     re.compile(
         r"(?:double|twice) the (?:number of )?(?:counters?|\+1/\+1)"
         r"|one additional (?:\+1/\+1 )?counter",
         re.I,
     )),

    # Extra combat
    ("extra_combat", "Extra combat phase",
     re.compile(
         r"additional combat phase"
         r"|second combat phase"
         r"|you may attack again this turn"
         r"|there is an additional combat",
         re.I,
     )),

    # Opponent restriction (stax)
    ("opponent_restriction", "Opponent restriction",
     re.compile(r"opponents? can't", re.I)),

    # Activated ability restriction (stax)
    ("activated_restriction", "Activated ability restriction",
     re.compile(r"activated abilit.{0,40}can't be activated", re.I)),

    # Tax effect (stax)
    ("tax_effect", "Tax effect",
     re.compile(r"spells?.{0,30}opponents?.{0,30}cost.{0,20}more", re.I)),

    # Enters tapped (stax)
    ("enters_tapped_opponent", "Opponents' permanents enter tapped",
     re.compile(
         r"(?:permanents?|lands?).{0,40}(?:opponents?|other players?).{0,30}enter.{0,15}tapped",
         re.I,
     )),

    # Monarch
    ("monarch", "Monarch mechanic",
     re.compile(r"\bmonarch\b", re.I)),

    # Initiative
    ("initiative", "Initiative mechanic",
     re.compile(r"\binitiative\b", re.I)),

    # Goad
    ("goad", "Goad",
     re.compile(r"\bgoad\b", re.I)),

    # Forced attack
    ("forced_attack", "Forced attack each combat",
     re.compile(r"attacks? each combat if able|all creatures attack each combat", re.I)),

    # Cascade / discover
    ("cascade", "Cascade / discover",
     re.compile(r"\bcascade\b|\bdiscover\b", re.I)),

    # Mana ability (Tyvar-style: rewards creatures with mana abilities)
    ("mana_dork", "Mana ability matters",
     re.compile(r"mana ability of this creature|mana ability", re.I)),

    # Tribal — elf (type-line check handled separately; oracle fallback here)
    ("tribal_elf", "Elf tribal",
     re.compile(r"\belves?\b", re.I)),

    # Counter trigger (Tyvar-style: puts counters equal to mana produced)
    ("counter_trigger", "Counter trigger (mana-based)",
     re.compile(r"\+1/\+1 counters?.{0,30}equal to.{0,30}mana", re.I)),
]

# ── DB helpers ────────────────────────────────────────────────────────────────

_QUERY = """
    SELECT id::text, name, oracle_text, type_line, color_identity, cmc, keywords
    FROM cards
    WHERE legalities->>'commander' = 'legal'
      AND (
          type_line ILIKE '%Legendary Creature%'
          OR type_line ILIKE '%Legendary Planeswalker%'
          OR oracle_text ILIKE '%can be your commander%'
      )
      AND name ILIKE %s
    ORDER BY name
    LIMIT 10
"""


def _fetch(name: str) -> list[dict]:
    if not DATABASE_URL:
        sys.exit("DATABASE_URL environment variable is required.")
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(_QUERY, (f"%{name}%",))
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


# ── Detection ─────────────────────────────────────────────────────────────────

def _detect(oracle_text: str, type_line: str) -> list[tuple[str, str, str]]:
    """Return list of (key, label, matched_phrase) for every firing pattern."""
    # Prepend type_line so tribal / type-check patterns can fire on it
    text = f"{type_line}\n{oracle_text}"
    seen: set[str] = set()
    hits: list[tuple[str, str, str]] = []
    for key, label, regex in ORACLE_PATTERNS:
        if key in seen:
            continue
        m = regex.search(text)
        if m:
            seen.add(key)
            hits.append((key, label, m.group(0).strip()))
    return hits


# ── Output ────────────────────────────────────────────────────────────────────

def _print_decomposition(card: dict) -> None:
    oracle_text = card.get("oracle_text") or ""
    type_line   = card.get("type_line") or ""
    hits = _detect(oracle_text, type_line)

    consumer: list[tuple[str, str, str]] = []
    producer: list[tuple[str, str, str]] = []
    todo:     list[tuple[str, str, str]] = []

    for key, label, phrase in hits:
        in_consumer = key in PATTERN_KEY_TO_CONSUMER_SQL
        in_producer = key in PATTERN_KEY_TO_PRODUCER_SQL
        if in_consumer:
            consumer.append((key, label, phrase))
        if in_producer:
            producer.append((key, label, phrase))
        if not in_consumer and not in_producer:
            todo.append((key, label, phrase))

    ci = "".join(card.get("color_identity") or []) or "C"
    print(f"\n{'═' * 60}")
    print(f"  {card['name']}  [{ci}]  {type_line}")
    print(f"{'═' * 60}")

    if oracle_text:
        for line in oracle_text.strip().splitlines():
            print(f"  {line}")
    print()

    _section("CONSUMER — deck needs these cards", consumer)
    _section("PRODUCER — deck amplifies this output", producer)
    _section("TODO — detected but no SQL entry yet", todo, dim=True)


def _section(
    title: str,
    rows: list[tuple[str, str, str]],
    dim: bool = False,
) -> None:
    if not rows:
        return
    prefix = "  · " if dim else "  ✓ "
    print(f"  {title}")
    print(f"  {'-' * (len(title))}")
    for key, label, phrase in rows:
        snippet = phrase[:60].replace("\n", " ")
        print(f"{prefix}{key:<30}  # {snippet}")
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Decompose a commander into consumer / producer mechanic keys."
    )
    parser.add_argument("name", help="Commander name (partial, case-insensitive)")
    args = parser.parse_args()

    cards = _fetch(args.name)
    if not cards:
        sys.exit(f"No legal commander found matching: {args.name!r}")

    for card in cards:
        _print_decomposition(card)


if __name__ == "__main__":
    main()
