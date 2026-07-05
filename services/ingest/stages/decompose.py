"""Decompose a commander into their mechanic roles using oracle text patterns.

For each matched pattern, the script checks whether that key is registered in
``synergy/commander_mechanics.py`` as a consumer key, a producer key, or both.
Patterns that fire but have no SQL entry yet are listed as "TODO" — those
represent gaps to fill in commander_mechanics.py.

Consumer  — the commander *needs* the deck full of these cards
Producer  — the commander *outputs* this; deck wants amplifiers

Usage
-----
    # Spot-check a single commander (print decomposition):
    docker compose run --rm ingest python -m stages.decompose "Tyvar the Bellicose"
    docker compose run --rm ingest python -m stages.decompose "Raggadragga"
    docker compose run --rm ingest python -m stages.decompose "Atraxa"
    # partial / case-insensitive match:
    docker compose run --rm ingest python -m stages.decompose tyvar

    # Write all commander decompositions to card_abilities (source='decompose'):
    docker compose run --rm ingest python -m stages.decompose --write
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

from mtg_sql import commanders
from regex_utils import p  # noqa: E402
from synergy.commander_mechanics import (
    DECK_KEY_LABELS,
    PATTERN_KEY_TO_CONSUMER_SQL,
    PATTERN_KEY_TO_PRODUCER_SQL,
    PRODUCER_DECOMPOSE_TO_DECK_KEY,
)
from synergy.tribal import TRIBES as _tribes

DATABASE_URL = os.environ.get("DATABASE_URL", "").replace(
    "postgresql+asyncpg://", "postgresql://"
)

# ── Oracle text detection patterns ───────────────────────────────────────────
# Each entry: (pattern_key, label, compiled_regex)
#
# These patterns detect WHAT a commander does; commander_mechanics.py then
# classifies each detected key as consumer or producer.

ORACLE_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    # ETB trigger
    (
        "etb_trigger",
        "ETB trigger",
        p(
            r"when(?:ever)?\s+"
            r"(?:(?:a |an |another |one or more )?(?:creature|permanent|token|land|artifact|enchantment)"
            r".{0,40}|.{2,50}?)"
            r"enters(?:\s+the battlefield)?",
        ),
    ),
    # Spell cast — creature
    # Type-based cast triggers accept two templatings:
    #   "whenever you cast <type>"                      — always a consumer
    #   "whenever a player casts <type> …, you <gain>"  — Niv-Mizzet, Parun:
    #     the trigger includes your own casts, but only counts when the
    #     payoff clause benefits *you*.  Without the ", you" guard this
    #     matches punisher text (Ruric Thar: "Whenever a player casts a
    #     noncreature spell, Ruric Thar deals 6 damage to them") whose deck
    #     avoids the type entirely.  "an opponent casts" never matches.
    *[
        (
            f"cast_trigger_{_key}",
            f"{_label} cast trigger",
            p(
                rf"when(?:ever)?\s+you cast (?:a |an )?{_type_re}"
                rf"|when(?:ever)?\s+a player casts (?:a |an )?{_type_re}[^.]{{0,60}}, you\s"
            ),
        )
        for _key, _label, _type_re in (
            ("creature", "Creature", r"creature"),
            ("instant_sorcery", "Instant/sorcery", r"(?:instant|sorcery|noncreature)"),
            ("enchantment", "Enchantment", r"enchantment"),
            ("artifact", "Artifact", r"artifact"),
            ("historic", "Historic spell", r"historic"),
            ("aura_equipment", "Aura or equipment", r"(?:aura|equipment)"),
        )
    ],
    # Spell cast — color-based
    # Per-color cast triggers — three phrasings covered per color:
    #   1. triggered: "whenever you cast a {color} spell" (standard trigger)
    #   2. static/post: "{color} spells you cast cost/have …" (Grand Arbiter, Zhulodok)
    #   3. static/pre: "cast {color} spells as though …" (Liberator, Urza's Battlethopter)
    *[
        (
            f"cast_trigger_{color}",
            f"{color.title()} spell cast trigger",
            p(
                rf"when(?:ever)?\s+you cast (?:a |an )?{color}\s+spell"
                rf"|{color} spells you cast"
                rf"|cast {color} spells"
            ),
        )
        for color in ("white", "blue", "black", "red", "green", "colorless")
    ],
    # Group hug
    (
        "group_hug",
        "Group hug",
        p(
            r"each player (?:draws?|may draw|may put)"
            r"|each player's draw step.{0,50}draws?"
            r"|\bparley\b",
        ),
    ),
    # Poison / infect / toxic
    (
        "poison_infect",
        "Poison / infect / toxic",
        p(r"\binfect\b|\bpoison counter|\btoxic\b"),
    ),
    # Equipment matters
    (
        "equipment_matters",
        "Equipment matters",
        p(
            r"equipped creature"
            r"|equipment (?:you control|attached|spell|token|are)"
            r"|target equipment"
            r"|aura or equipment"
            r"|aura,?\s+and equipment"
            r"|aura,\s+equipment",
        ),
    ),
    # Artifact count
    (
        "artifact_count",
        "Artifact count matters",
        p(r"for each (?:tapped )?artifact you control|artifacts you control"),
    ),
    # Artifact creatures
    (
        "artifact_creatures",
        "Artifact creatures matter",
        p(r"artifact creatures? you control"),
    ),
    # Death trigger
    # Standard: "whenever a/another creature dies"
    # Teysa Karlov: "if a creature dying causes a triggered ability … to trigger,
    #   that ability triggers an additional time" — she amplifies death triggers
    #   rather than having one, but her deck plan is identical: fill with sac
    #   outlets, death payoffs, and self-sacrifice fodder.
    (
        "death_trigger",
        "Death trigger",
        p(
            r"when(?:ever)?\s+(?:a |an |another |one or more )?(?:nontoken )?creature"
            r".{0,40}dies"
            r"|creature dying causes",
        ),
    ),
    # Graveyard from play
    (
        "graveyard_from_play",
        "Permanent to graveyard trigger",
        p(
            r"when(?:ever)?\s+(?:a |an )?(?:nontoken )?permanent.{0,40}"
            r"(?:put into|goes to|enters?) (?:a |your )?graveyard",
        ),
    ),
    # Attack trigger
    (
        "attack_trigger",
        "Attack trigger",
        p(
            r"when(?:ever)?\s+"
            r"(?:this creature|one or more creatures you control|a creature you control|you"
            r"|.{2,50}?)"
            r"\s+attacks?(?:\s+alone)?",
        ),
    ),
    # Combat damage to player
    (
        "combat_damage_to_player",
        "Combat damage to player",
        p(r"deals? combat damage to (?:a |an )?(?:player|opponent)"),
    ),
    # High-MV payoff — commander scales an effect from the mana value of a card
    # (Yuriko: opponents lose life equal to the revealed card's mana value;
    #  Zhulodok: grants cascade to colorless spells with mana value 7 or greater;
    #  Kozilek: "Discard a card with mana value X: Counter target spell with
    #  mana value X" — the deck wants an MV spread topping out high).
    # Deck wants the highest-MV spells possible to maximise the trigger.
    (
        "high_mv_payoff",
        "High mana value payoff",
        p(
            r"(?:damage|lose life|loses? life).{0,40}mana value"
            r"|mana value.{0,40}(?:damage|lose life|loses? life)"
            r"|mana value \d+ or greater"
            r"|discard a card with mana value x"
        ),
    ),
    # Activated tutor engines — a repeatable activated ability ({cost}: …)
    # that searches the library.  Yisan, Prime Speaker Vannifar, Captain
    # Sisay, Zirilan: canonical remove-on-sight engine commanders (see
    # profile.py ACTIVATED_ENGINE_KEYS).  The creature-target variant also
    # gets consumer SQL (the deck supplies the creature toolbox); other
    # targets are signal-only until their consumers are added (#136).
    (
        "activated_tutor_creature",
        "Activated creature-tutor engine",
        p(
            r"\{[^}]+\}[^:.]{0,80}:[^.]{0,20}search your library for "
            r"(?:a |an |up to \S+ )?[^.]{0,30}creature card"
        ),
    ),
    (
        "activated_tutor",
        "Activated tutor engine",
        p(r"\{[^}]+\}[^:.]{0,80}:[^.]{0,20}search your library"),
    ),
    # Madness payoff
    ("madness_payoff", "Madness payoff", p(r"\bmadness\b|for its madness cost")),
    # Discard outlet
    (
        "discard_outlet",
        "Discard outlet",
        p(r"discard (?:a |one or more )?(?:card|cards)"),
    ),
    # Sacrifice payoff
    (
        "sacrifice_payoff",
        "Sacrifice payoff",
        p(
            r"when(?:ever)?\s+you sacrifice"
            r"|sacrifice (?:a |an |another )?(?:creature|permanent)",
        ),
    ),
    # Landfall
    (
        "landfall",
        "Landfall",
        p(r"\blandfall\b|when(?:ever)?\s+(?:a |one or more )?land.{0,20}enters"),
    ),
    # Counter placement — commander puts +1/+1 counters on things.
    # Producer key: deck wants counter_trigger amplifiers (Hardened Scales, etc.)
    (
        "counter_placement",
        "Counter placement",
        p(
            r"put (?:a |one or more |an? )?\+1/\+1 counter|\+1/\+1 counters on it equal to the"
        ),
    ),
    # Lifegain producer — commander outputs life gain (e.g. Sythis, Oloro)
    # Matches "you gain N life" as a primary effect, not as a trigger condition.
    (
        "lifegain_producer",
        "Lifegain producer",
        p(r"you gain \d+ life|you gain (?:life equal|X life|that much life)"),
    ),
    # Lifegain trigger — commander reacts to life being gained
    (
        "lifegain_trigger",
        "Life gain trigger",
        p(r"when(?:ever)?\s+you (?:gain|gained) life"),
    ),
    # Draw producer — commander outputs card draw (e.g. Sythis, Edric)
    # Matches "draw a card" / "draw X cards" as a primary effect.
    (
        "draw_producer",
        "Draw producer",
        p(r"draw (?:a card|cards?|X cards?|that many cards?|(?:one|two|three) cards?)"),
    ),
    # Draw trigger — commander reacts to drawing cards
    (
        "draw_trigger",
        "Draw trigger",
        p(
            r"when(?:ever)?\s+you draw (?:a card|cards|your (?:first|second|third) card)",
        ),
    ),
    # Token generator — commander creates tokens as a primary output.
    # creature_token_generator fires for creature tokens (Krenko, Edgar Markov).
    # artifact_token_generator fires for non-creature tokens (Clue, Blood,
    # Treasure, Mutagen) — uses a negative lookahead to exclude "creature token".
    (
        "creature_token_generator",
        "Creature token generator",
        p(
            r"create (?:x |a number of |one or more |that many |(?:\d+ ))?(?:[\w/]+ )*creature tokens?"
        ),
    ),
    (
        "artifact_token_generator",
        "Artifact token generator",
        p(
            r"create (?:x |a number of |one or more |that many |(?:\d+ ))?(?:(?!creature\b)[\w/]+ )*tokens?"
        ),
    ),
    (
        "token_generator",
        "Token generator",
        p(
            r"create (?:x |a number of |one or more |that many |(?:\d+ ))?(?:[\w/]+ )*tokens?"
        ),
    ),
    # Token trigger
    (
        "token_trigger",
        "Token creation trigger",
        p(
            r"when(?:ever)?\s+(?:one or more )?tokens? (?:enters?|(?:is |are )?created|(?:is |are )?put)",
        ),
    ),
    # Trigger doubling
    (
        "trigger_doubling",
        "Trigger doubling",
        p(r"triggers? an additional time|triggers? twice"),
    ),
    # Proliferate
    ("proliferate_matters", "Proliferate", p(r"\bproliferate\b")),
    # Second spell
    (
        "second_spell",
        "Second spell matters",
        p(
            r"second spell (?:each turn|you cast this turn)"
            r"|when(?:ever)?\s+you cast your second",
        ),
    ),
    # Punisher
    (
        "punisher",
        "Punisher effect",
        p(
            r"each opponent (?:loses? \d+ life|takes? \d+ damage)"
            r"|deals? \d+ damage to each opponent",
        ),
    ),
    # Weenie matters
    (
        "weenie_matters",
        "Weenie matters",
        p(
            r"power (?:of )?(?:1|2|one|two) or less"
            r"|creatures? with power (?:1|2|one|two) or less",
        ),
    ),
    # Unearth / encore
    (
        "unearth_encore",
        "Unearth / encore / temporary reanimation",
        p(
            r"\bunearth\b|\bencore\b"
            r"|(?:exile|sacrifice) (?:it|them) at the beginning of the next end step",
        ),
    ),
    # Graveyard payoff — both word orders: "from your graveyard … cast" AND
    # the canonical "cast/play … from your graveyard" (Muldrotha: "cast a
    # permanent spell of each permanent type from your graveyard"; Karador:
    # "cast a creature spell from your graveyard").
    (
        "graveyard_payoff",
        "Graveyard payoff",
        p(
            r"from (?:your |a |the )?graveyard.{0,30}(?:cast|play|battlefield)"
            r"|(?:cast|play)(?: a| an| up to)? .{0,60}?from (?:your |a |the )?graveyard"
            r"|when.{0,30}put into (?:a |your )?graveyard from",
        ),
    ),
    # Cycling trigger
    (
        "cycling_trigger",
        "Cycling trigger",
        p(r"when(?:ever)?\s+(?:a player )?(?:cycles?|discards?) (?:a |this )?card"),
    ),
    # Counter doubler
    (
        "counter_doubler",
        "Counter doubler",
        p(
            r"(?:double|twice) the (?:number of )?(?:counters?|\+1/\+1)"
            r"|one additional (?:\+1/\+1 )?counter",
        ),
    ),
    # Extra combat
    (
        "extra_combat",
        "Extra combat phase",
        p(
            r"additional combat phase"
            r"|second combat phase"
            r"|you may attack again this turn"
            r"|there is an additional combat",
        ),
    ),
    # Opponent restriction (stax)
    ("opponent_restriction", "Opponent restriction", p(r"opponents? can't")),
    # Activated ability restriction (stax)
    (
        "activated_restriction",
        "Activated ability restriction",
        p(r"activated abilit.{0,40}can't be activated"),
    ),
    # Tax effect (stax)
    ("tax_effect", "Tax effect", p(r"spells?.{0,30}opponents?.{0,30}cost.{0,20}more")),
    # Enters tapped (stax)
    (
        "enters_tapped_opponent",
        "Opponents' permanents enter tapped",
        p(
            r"(?:permanents?|lands?).{0,40}(?:opponents?|other players?).{0,30}enter.{0,15}tapped",
        ),
    ),
    # Monarch
    ("monarch", "Monarch mechanic", p(r"\bmonarch\b")),
    # Initiative
    ("initiative", "Initiative mechanic", p(r"\binitiative\b")),
    # Goad
    ("goad", "Goad", p(r"\bgoad\b")),
    # Forced attack
    (
        "forced_attack",
        "Forced attack each combat",
        p(r"attacks? each combat if able|all creatures attack each combat"),
    ),
    # Cascade / discover
    ("cascade", "Cascade / discover", p(r"\bcascade\b|\bdiscover\b")),
    # Mana ability (Tyvar-style: rewards creatures with mana abilities)
    (
        "mana_dork",
        "Mana ability matters",
        p(r"mana ability of this creature|mana ability"),
    ),
    # Tribal — all supported tribes (oracle-text fallback; type-line handled in SQL)
    *[
        (
            f"tribal_{_tribe}",
            f"{_tribe.title()} tribal",
            p(f"\\b(?:{_oracle_regex})\\b"),
        )
        for _tribe, _oracle_regex in _tribes
    ],
]

# ── DB helpers ────────────────────────────────────────────────────────────────

_QUERY = (
    "SELECT id::text, name, oracle_text, type_line, color_identity, cmc, keywords"
    " FROM cards"
    f" WHERE {commanders.WHERE}"
    " AND name ILIKE %s"
    " ORDER BY name LIMIT 10"
)


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
    seen: set[str] = set()
    hits: list[tuple[str, str, str]] = []
    for key, label, regex in ORACLE_PATTERNS:
        if key in seen:
            continue
        m = regex.search(oracle_text)
        if m:
            seen.add(key)
            hits.append((key, label, m.group(0).strip()))
    return hits


# ── Output ────────────────────────────────────────────────────────────────────


def _print_decomposition(card: dict) -> None:
    oracle_text = card.get("oracle_text") or ""
    type_line = card.get("type_line") or ""
    hits = _detect(oracle_text, type_line)

    consumer: list[tuple[str, str, str]] = []
    producer: list[tuple[str, str, str]] = []
    todo: list[tuple[str, str, str]] = []

    for key, label, phrase in hits:
        in_consumer = key in PATTERN_KEY_TO_CONSUMER_SQL
        in_producer = key in PRODUCER_DECOMPOSE_TO_DECK_KEY
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
    _section("PRODUCER — deck amplifies this output", producer, producer=True)
    _section("TODO — detected but no SQL entry yet", todo, dim=True)


def _section(
    title: str,
    rows: list[tuple[str, str, str]],
    dim: bool = False,
    producer: bool = False,
) -> None:
    if not rows:
        return
    prefix = "  · " if dim else "  ✓ "
    print(f"  {title}")
    print(f"  {'-' * (len(title))}")
    for key, label, phrase in rows:
        snippet = phrase[:60].replace("\n", " ")
        if producer and key in PRODUCER_DECOMPOSE_TO_DECK_KEY:
            deck_keys = PRODUCER_DECOMPOSE_TO_DECK_KEY[key]
            parts = [f"{dk} ({DECK_KEY_LABELS.get(dk, dk)})" for dk in deck_keys]
            suffix = f"  → deck needs: {', '.join(parts)}"
        else:
            suffix = ""
        print(f"{prefix}{key:<30}  # {snippet}{suffix}")
    print()


# ── DB write ──────────────────────────────────────────────────────────────────

_ALL_COMMANDERS_QUERY = (
    f"SELECT id::text, oracle_text, type_line FROM cards WHERE {commanders.WHERE}"
)

_UPSERT = """
    INSERT INTO card_abilities
        (card_id, ability_type, ability_name, trigger_event, effect_class, raw_text, source)
    VALUES
        (%(card_id)s::uuid, %(ability_type)s, %(ability_name)s,
         %(trigger_event)s, NULL, %(raw_text)s, 'decompose')
    ON CONFLICT (card_id, ability_type, ability_name, COALESCE(effect_class, ''))
    DO UPDATE SET
        raw_text      = EXCLUDED.raw_text,
        trigger_event = EXCLUDED.trigger_event
"""


def write_commander_abilities() -> None:
    """Upsert card_abilities rows for all legal commanders (source='decompose').

    Runs _detect() on each commander's oracle text and writes one row per
    matched pattern.  Safe to re-run — uses DO UPDATE so stale rows are
    refreshed.

    ability_type heuristic (mirrors tag.py):
        'triggered'  if 'trigger' in label.lower()
        'static'     otherwise
    """
    if not DATABASE_URL:
        sys.exit("DATABASE_URL environment variable is required.")

    import logging

    log = logging.getLogger(__name__)

    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(_ALL_COMMANDERS_QUERY)
            all_commanders = cur.fetchall()

        log.info("write_commander_abilities: %d legal commanders", len(all_commanders))

        rows: list[dict] = []
        for row in all_commanders:
            card_id = row["id"]
            oracle_text = row["oracle_text"] or ""
            type_line = row["type_line"] or ""

            for key, label, phrase in _detect(oracle_text, type_line):
                ability_type = "triggered" if "trigger" in label.lower() else "static"
                rows.append(
                    {
                        "card_id": card_id,
                        "ability_type": ability_type,
                        "ability_name": label,
                        "trigger_event": key,
                        "raw_text": phrase[:200],
                    }
                )

        if not rows:
            log.warning(
                "write_commander_abilities: no patterns matched — nothing written"
            )
            return

        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, _UPSERT, rows, page_size=500)
        conn.commit()

        log.info(
            "write_commander_abilities: upserted %d card_abilities rows "
            "across %d commanders",
            len(rows),
            len({r["card_id"] for r in rows}),
        )
    finally:
        conn.close()


# ── CLI ───────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Decompose a commander into consumer / producer mechanic keys."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "name",
        nargs="?",
        help="Commander name (partial, case-insensitive) — prints decomposition to stdout.",
    )
    group.add_argument(
        "--write",
        action="store_true",
        help=(
            "Write all commander decompositions to card_abilities "
            "(source='decompose').  Idempotent."
        ),
    )
    args = parser.parse_args()

    if args.write:
        import logging

        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
        )
        write_commander_abilities()
        return

    cards = _fetch(args.name)
    if not cards:
        sys.exit(f"No legal commander found matching: {args.name!r}")

    for card in cards:
        _print_decomposition(card)


if __name__ == "__main__":
    main()
