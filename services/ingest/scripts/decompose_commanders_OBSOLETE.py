"""
Decompose all ~3000 legal commanders into structured synergy signals.

Two complementary sources are combined per commander:

1. **Oracle text patterns** — regex against the commander's rules text, same
   approach as the existing synergy/ modules.
2. **XMage ability + effect classes** — ``xmage_parse.parse_java_file()``
   returns the Java ability class names (e.g. ``DiesCreatureTriggeredAbility``)
   and effect class names (e.g. ``DamagePlayersEffect``) from the game-engine
   implementation.  These are translated via ``ABILITY_CLASS_TO_EVENT`` and
   ``EFFECT_CLASS_TO_EFFECT``.  XMage data is ground-truth where available but
   not every commander has a Java implementation.

Output
------
``/data/commander_decomposition.json``  (ingest_cache volume)

Each entry::

    {
      "id": "<uuid>",
      "name": "Syr Konrad, the Grim",
      "oracle_text": "...",
      "color_identity": ["B"],
      "cmc": 5.0,
      "type_line": "Legendary Creature — Human Knight",
      "xmage_file": "SyrKonradTheGrim.java",   # null if not found
      "signals": [
        {
          "pattern_key": "death_trigger",
          "label": "Death trigger",
          "source": "oracle_text",
          "matched_phrase": "whenever another creature dies",
          "score": 0.9
        },
        {
          "pattern_key": "dies",
          "label": "Dies trigger (XMage)",
          "source": "xmage",
          "ability_class": "DiesCreatureTriggeredAbility",
          "effect_class": "damage",
          "score": 0.85
        }
      ],
      "unmatched_triggers": []   # oracle-text clauses that fired no pattern
    }

Usage
-----
Via Docker (recommended)::

    docker compose run --rm ingest python scripts/decompose_commanders.py

Direct (requires DATABASE_URL and XMage source tree)::

    python scripts/decompose_commanders.py --xmage-dir /path/to/mage

Options::

    --xmage-dir DIR   XMage repository root (default: /mage or $XMAGE_DIR)
    --out FILE        Output path (default: /data/commander_decomposition.json)
    --no-xmage        Skip XMage lookup entirely (oracle text only)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from collections import Counter
from pathlib import Path

import psycopg2
import psycopg2.extras

# ── Import XMage parser from parent package ───────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))
from xmage_parse import (
    ABILITY_CLASS_TO_EVENT,
    EFFECT_CLASS_TO_EFFECT,
    SPELLCAST_FILTER_MAP,
    _normalize,
    parse_java_file,
)

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Strip asyncpg scheme prefix so psycopg2 can use the URL directly.
DATABASE_URL = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")

# ── Oracle text pattern library ───────────────────────────────────────────────
# Each entry: (pattern_key, label, compiled_regex, score)
#
# Patterns are checked in order; a commander can match multiple patterns.
# Keep patterns specific enough to avoid false positives but broad enough
# to cover the common phrasings of each ability family.

ORACLE_PATTERNS: list[tuple[str, str, re.Pattern, float]] = [
    # ETB triggers — commander rewards creatures/permanents entering.
    # Two subject forms:
    #   Generic: "whenever a/an/another/one or more creature/permanent/token ... enters"
    #   Proper-name: "when(ever) <CardName> enters" — the card refers to itself by name,
    #   standard wording for legendary creatures printed before ~2020.
    ("etb_trigger",
     "ETB trigger",
     re.compile(
         r"when(?:ever)?\s+"
         r"(?:"
         r"(?:a |an |another |one or more )?(?:creature|permanent|token|land|artifact|enchantment)"
         r".{0,40}"           # generic subject + optional qualifier
         r"|.{2,50}?"         # proper-name subject (lazy — matches minimum before "enters")
         r")"
         r"enters(?:\s+the battlefield)?",
         re.I,
     ),
     0.9),

    # Spell cast — creature spells specifically
    ("cast_trigger_creature",
     "Creature cast trigger",
     re.compile(r"when(?:ever)?\s+you cast (?:a |an )?creature", re.I),
     0.9),

    # Spell cast — instant or sorcery
    ("cast_trigger_instant_sorcery",
     "Instant/sorcery cast trigger",
     re.compile(r"when(?:ever)?\s+you cast (?:a |an )?(?:instant|sorcery|noncreature)", re.I),
     0.9),

    # Spell cast — enchantment
    ("cast_trigger_enchantment",
     "Enchantment cast trigger",
     re.compile(r"when(?:ever)?\s+you cast (?:a |an )?enchantment", re.I),
     0.9),

    # Spell cast — artifact
    ("cast_trigger_artifact",
     "Artifact cast trigger",
     re.compile(r"when(?:ever)?\s+you cast (?:a |an )?artifact", re.I),
     0.9),

    # Spell cast — historic (artifacts, legendaries, and Sagas).
    # Distinct from cast_trigger_artifact: historic includes legendary
    # permanents and Sagas.  Key commanders: Jhoira, Teshar, Sarah Jane Smith,
    # The Sixth Doctor, Moira and Teshar, Glóin, Alistair, Surtr, Basim.
    # (~10 commanders with the "whenever you cast a historic spell" trigger)
    ("cast_trigger_historic",
     "Historic spell cast trigger",
     re.compile(r"when(?:ever)?\s+you cast (?:a |an )?historic", re.I),
     0.9),

    # Group hug — commander actively gives resources (card draw, mana, tokens,
    # lands) to all players, creating a political board state where everyone
    # benefits.  Producers: Howling Mine variants, Dictate of Kruphix, land
    # ramp for all, token generators for all.
    #
    # Three signal forms:
    #   "each player draws/may draw/may put" — direct resource grant
    #   "each player's draw step...draws"    — structured draw step trigger
    #                                          (Kami, Nekusar); excludes
    #                                          Maralen / Mornsong Aria which
    #                                          also use "draw step" but prevent
    #                                          drawing rather than grant it
    #   "parley"                             — Selvala, Phabine mechanic
    #
    # (~11 commanders: Kami, Kwain, Kynaios and Tiro, Selvala, The Second
    # Doctor, Winter, Nekusar, Grothama, Phabine, Círdan, Jace Beleren)
    ("group_hug",
     "Group hug",
     re.compile(
         r"each player (?:draws?|may draw|may put)"
         r"|each player's draw step.{0,50}draws?"
         r"|\bparley\b",
         re.I,
     ),
     0.8),

    # Poison / infect / toxic — commander interacts with the poison-counter
    # win condition.  All three keywords describe the same underlying mechanic:
    #   infect: damage → -1/-1 counters on creatures, poison counters on players
    #   toxic N: combat damage to a player also gives N poison counters
    #   poison counter: direct reference (Fynn, Ixhel Corrupted clause, etc.)
    # Covers: Skithiryx (infect), Fynn (deathtouch → poison), Ixhel/Vishgraz/
    # Karumonix/Skrelv/Venser/Ria Ivor/Kinzu (toxic), Vraska (poison ultimate),
    # both Meliras (hate/interaction with the mechanic).
    # (~12 commanders)
    ("poison_infect",
     "Poison / infect / toxic",
     re.compile(r"\binfect\b|\bpoison counter|\btoxic\b", re.I),
     0.9),

    # Equipment matters — commander cares about Equipment being attached,
    # entering, or equipped creatures attacking.  Covers:
    #   "equipped creature" — attack triggers (Akiri, Syr Gwyn, Nahiri, Cloud)
    #   "Equipment you control" — ETB / static (Barret, Chishiro, Kemba, Balan)
    #   "Equipment attached" — counter-per-equipment (Wyleth, Kemba Regent)
    #   "Equipment spell" — cost-reduction (Danitha Capashen, Sokka, Cid)
    #   "target Equipment" — attach-on-attack (Ardenn, Raubahn, Amy Rose)
    #   "Aura or Equipment" / "Aura, Equipment" — broad (Sram, Galea, Tiana)
    # (~60+ commanders including Kemba, Sram, Galea, Wyleth, Balan, Bruenor)
    ("equipment_matters",
     "Equipment matters",
     re.compile(
         r"equipped creature"
         r"|equipment (?:you control|attached|spell|token|are)"
         r"|target equipment"
         r"|aura or equipment"
         r"|aura,?\s+and equipment"
         r"|aura,\s+equipment",
         re.I,
     ),
     0.8),

    # Artifact count matters — commander scales with number of artifacts you
    # control (static bonus, damage, cost reduction, etc.).  Distinct from
    # cast_trigger_artifact (which fires on cast) and equipment_matters (which
    # cares about attached/equipped).  Producers: cheap artifacts, mana rocks,
    # artifact tokens, Vehicles.
    # Covers:
    #   "for each artifact you control"     — Akiri, Saheeli ultimate
    #   "for each tapped artifact you control" — Alibou (also fires artifact_creatures)
    #   "artifacts you control"             — Muzzio (greatest mana value among)
    # NOT matched:
    #   "tap an untapped artifact you control" — Urza mana tap (singular, no "for each")
    #   "artifact creatures you control"    — covered by artifact_creatures pattern
    # (~10+ commanders: Akiri Line-Slinger, Saheeli the Gifted, Muzzio,
    #  Kurkesh, Daretti, etc.)
    ("artifact_count",
     "Artifact count matters",
     re.compile(r"for each (?:tapped )?artifact you control|artifacts you control", re.I),
     0.9),

    # Artifact creatures matters — commander buffs, enables, or triggers off
    # artifact creatures specifically.  Distinct from artifact_count (generic
    # artifact scaling) and cast_trigger_artifact (cast trigger).
    # Producers: artifact creature tokens, Myr, Constructs, Servos, Thopters.
    # (~10+ commanders: Alibou, Brudiclad, Padeem, Teshar, Sydri, Memnarch, etc.)
    ("artifact_creatures",
     "Artifact creatures matter",
     re.compile(r"artifact creatures? you control", re.I),
     0.9),

    # Death / dies trigger
    ("death_trigger",
     "Death trigger",
     re.compile(r"when(?:ever)?\s+(?:a |an |another |one or more )?(?:nontoken )?creature"
                r".{0,40}dies", re.I),
     0.9),

    # Any permanent put into graveyard (broader than creature-only death)
    ("graveyard_from_play",
     "Permanent to graveyard trigger",
     re.compile(r"when(?:ever)?\s+(?:a |an )?(?:nontoken )?permanent.{0,40}"
                r"(?:put into|goes to|enters?) (?:a |your )?graveyard", re.I),
     0.8),

    # Attack triggers — when this creature/a creature/the commander attacks.
    # Two subject forms:
    #   Generic: "this creature", "a creature you control", "one or more creatures you control", "you"
    #   Proper-name: "whenever <CardName> attacks [alone]" — self-referential wording.
    ("attack_trigger",
     "Attack trigger",
     re.compile(
         r"when(?:ever)?\s+"
         r"(?:this creature|one or more creatures you control|a creature you control|you"
         r"|.{2,50}?"         # proper-name subject (lazy)
         r")"
         r"\s+attacks?(?:\s+alone)?",
         re.I,
     ),
     0.8),

    # Combat damage to a player
    ("combat_damage_to_player",
     "Combat damage to player",
     re.compile(r"deals? combat damage to (?:a |an )?(?:player|opponent)", re.I),
     0.9),

    # Madness payoff — discard outlet that cares about the Madness keyword
    ("madness_payoff",
     "Madness payoff",
     re.compile(r"\bmadness\b|for its madness cost", re.I),
     1.0),

    # Discard outlet (may or may not be madness-specific)
    ("discard_outlet",
     "Discard outlet",
     re.compile(r"discard (?:a |one or more )?(?:card|cards)", re.I),
     0.7),

    # Sacrifice payoff / outlet
    ("sacrifice_payoff",
     "Sacrifice payoff",
     re.compile(r"when(?:ever)?\s+you sacrifice|sacrifice (?:a |an |another )?(?:creature|permanent)", re.I),
     0.8),

    # Landfall
    ("landfall",
     "Landfall",
     re.compile(r"\blandfall\b|when(?:ever)?\s+(?:a |one or more )?land.{0,20}enters", re.I),
     0.9),

    # +1/+1 counter placement
    ("counter_placement",
     "Counter placement",
     re.compile(r"put (?:a |one or more |an? )?\+1/\+1 counter", re.I),
     0.8),

    # Lifegain trigger
    ("lifegain_trigger",
     "Life gain trigger",
     re.compile(r"when(?:ever)?\s+you (?:gain|gained) life", re.I),
     0.8),

    # Draw trigger
    ("draw_trigger",
     "Draw trigger",
     re.compile(r"when(?:ever)?\s+you draw (?:a card|cards|your (?:first|second|third) card)", re.I),
     0.8),

    # Token creation trigger
    ("token_trigger",
     "Token creation trigger",
     re.compile(r"when(?:ever)?\s+(?:one or more )?tokens? (?:enters?|(?:is |are )?created|"
                r"(?:is |are )?put)", re.I),
     0.8),

    # Trigger doubling
    ("trigger_doubling",
     "Trigger doubling",
     re.compile(r"triggers? an additional time|triggers? twice", re.I),
     0.9),

    # Proliferate
    ("proliferate_matters",
     "Proliferate",
     re.compile(r"\bproliferate\b", re.I),
     0.8),

    # Second spell matters
    ("second_spell",
     "Second spell matters",
     re.compile(r"second spell (?:each turn|you cast this turn)|"
                r"when(?:ever)?\s+you cast your second", re.I),
     0.9),

    # Punisher — deals damage or drains life to each opponent on trigger
    ("punisher",
     "Punisher effect",
     re.compile(r"each opponent (?:loses? \d+ life|takes? \d+ damage)|"
                r"deals? \d+ damage to each opponent", re.I),
     0.9),

    # Weenie matters — cares about low-power creatures
    ("weenie_matters",
     "Weenie matters",
     re.compile(r"power (?:of )?(?:1|2|one|two) or less|"
                r"creatures? with power (?:1|2|one|two) or less", re.I),
     0.8),

    # Unearth / encore / Feldon-style — commanders that grant or reference
    # temporary graveyard-recursion effects.  All three share the same
    # mechanical signature: returned/copied creature has haste and is
    # exiled or sacrificed at the beginning of the next end step.
    # Producers: creatures with unearth or encore printed on them; haste
    # enablers; ETB payoffs that re-trigger on the returned creature.
    # Arm 1 — keyword grants: Sedris ("each creature card in your graveyard
    #   has unearth"), Burakos (encore), etc.
    # Arm 2 — inline phrasing: Feldon of the Third Path ("sacrifice it at
    #   the beginning of the next end step") — same effect, no keyword.
    ("unearth_encore",
     "Unearth / encore / temporary reanimation",
     re.compile(
         r"\bunearth\b|\bencore\b"
         r"|(?:exile|sacrifice) (?:it|them) at the beginning of the next end step",
         re.I,
     ),
     0.9),

    # Graveyard payoff — casting from or returning from graveyard
    ("graveyard_payoff",
     "Graveyard payoff",
     re.compile(r"from (?:your |a |the )?graveyard.{0,30}(?:cast|play|battlefield)|"
                r"when.{0,30}put into (?:a |your )?graveyard from", re.I),
     0.8),

    # Keyword lord — grants a keyword to creatures you control
    ("keyword_lord",
     "Keyword grant (lord)",
     re.compile(r"(?:creatures? you control|other [a-z\s]+you control).{0,40}"
                r"(?:gain|have|get) (?:flying|trample|haste|menace|hexproof|lifelink|"
                r"deathtouch|reach|vigilance|indestructible|first strike|double strike)", re.I),
     0.8),

    # Cycling / discard-to-draw triggers
    ("cycling_trigger",
     "Cycling trigger",
     re.compile(r"when(?:ever)?\s+(?:a player )?(?:cycles?|discards?) (?:a |this )?card", re.I),
     0.8),

    # Counter doublers / proliferate payoffs
    ("counter_doubler",
     "Counter doubler",
     re.compile(r"(?:double|twice) the (?:number of )?(?:counters?|\+1/\+1)|"
                r"one additional (?:\+1/\+1 )?counter", re.I),
     0.9),

    # Extra combat phases — commander grants additional attack steps.
    # Covers both the clause form ("there is an additional combat phase after
    # this phase") and the direct grant form ("you may have a second combat
    # phase"), as well as the rare "you may attack again" wording.
    # Examples: Raiyuu (if it's the first combat phase…), Aggravated Assault,
    # Savage Beating, Moraug, Fury of Akoum, Isshin (two attacks → two extra
    # combat triggers, though the extra-combat clause itself is on other cards).
    ("extra_combat",
     "Extra combat phase",
     re.compile(
         r"additional combat phase"
         r"|second combat phase"
         r"|you may attack again this turn"
         r"|there is an additional combat",
         re.I,
     ),
     0.9),

    # Color-based cast triggers — commander rewards casting spells of a
    # specific color (or multicolored / colorless).  Distinct from type-based
    # cast triggers; requires a color word immediately before "spell".
    # Examples: Chandra ("whenever you cast a red spell"),
    # Zada Hedron Grinder ("whenever you cast an instant or sorcery spell that
    # targets only Zada") — note Zada uses type, not color, so won't match here.
    # Pia Nalaar, Shortsighted ("whenever you cast a red or artifact spell").
    ("cast_trigger_colored",
     "Color-based cast trigger",
     re.compile(
         r"when(?:ever)?\s+you cast (?:a |an )?"
         r"(?:red|blue|green|white|black|colorless|multicolored|monocolored)"
         r"(?:\s+or\s+(?:red|blue|green|white|black|colorless|multicolored|artifact|creature))?"
         r"\s+spell",
         re.I,
     ),
     0.9),

    # ── Stax / hatebear patterns ──────────────────────────────────────────────

    # Opponent restriction — blanket "opponents can't" clause.
    # Covers: Narset Parter of Veils ("opponents can't draw more than one card"),
    # Dragonlord Dromoka ("your opponents can't cast spells during your turn"),
    # Jin-Gitaxias ("opponents can't cast spells during your draw step"), etc.
    ("opponent_restriction",
     "Opponent restriction",
     re.compile(r"opponents? can't", re.I),
     0.8),

    # Activated ability restriction — prevents opponents from using activated
    # abilities.  Covers: Linvala Keeper of Silence, Karn the Great Creator,
    # Koma Cosmos Serpent, Drana and Linvala.
    ("activated_restriction",
     "Activated ability restriction",
     re.compile(r"activated abilit.{0,40}can't be activated", re.I),
     0.8),

    # Tax effect — opponents' spells cost more.
    # Covers: Grand Arbiter Augustin IV, Hinata Dawn-Crowned, Edgewall Innkeeper
    # analogues that tax opponents.  Broad enough to catch "spells cost {1} more".
    ("tax_effect",
     "Tax effect",
     re.compile(r"spells?.{0,30}opponents?.{0,30}cost.{0,20}more", re.I),
     0.8),

    # Opponents' permanents / lands enter tapped.
    # Covers: Thalia Heretic Cathar, Reidane God of the Worthy,
    # Archon of Emeria, Loran of the Third Path (lands only variant).
    ("enters_tapped_opponent",
     "Opponents' permanents enter tapped",
     re.compile(
         r"(?:permanents?|lands?).{0,40}(?:opponents?|other players?).{0,30}enter.{0,15}tapped",
         re.I,
     ),
     0.8),

    # ── Forced / incentivized combat patterns ─────────────────────────────────

    # Monarch — political mechanic: you draw a card each turn while monarch;
    # lose the crown when a creature deals combat damage to you.  Creates a
    # natural incentive for opponents to attack and for you to defend (or
    # take it back via combat).  Producers: Swords and shields, pillowfort,
    # creatures that trigger off being attacked, token generators.
    # Covers all phrasings: "become the monarch", "you're the monarch",
    # "if you're the monarch", etc.  (~7 commanders)
    ("monarch",
     "Monarch mechanic",
     re.compile(r"\bmonarch\b", re.I),
     0.9),

    # Initiative — Baldur's Gate mechanic: take the initiative to advance
    # through the Undercity dungeon; retain it until an opponent attacks you
    # with a creature that deals damage.  Similar political pressure to
    # monarch.  Producers: efficient combat creatures, protection effects.
    # (~4 commanders including Rilsa Rael, Safana, Rasaad yn Bashir)
    ("initiative",
     "Initiative mechanic",
     re.compile(r"\binitiative\b", re.I),
     0.9),

    # Goad — forces target creature to attack each combat if able, and to
    # attack a player other than the goading player.  Commander-level goad
    # turns the table into a battleground and rewards you for keeping
    # opponents fighting each other.  Producers: wide token swarms, flash
    # creatures, instants that tap-or-destroy blockers.
    # (~24 commanders: Karazikar, Marisi, Kitt Kanto, Kardur, Kaima, etc.)
    ("goad",
     "Goad",
     re.compile(r"\bgoad\b", re.I),
     0.9),

    # Forced attack — creatures (often the commander itself, or all creatures)
    # must attack each combat if able.  Distinct from goad: this is a blanket
    # rule on the board rather than a targeted effect.  Commanders in this
    # bucket want creatures that are good at attacking but can survive doing
    # so every turn: haste enablers, equipment, protection, extra combats.
    # Covers both self-forcing ("Zurgo attacks each combat if able") and
    # global-forcing ("All creatures attack each combat if able" — Thantis,
    # Kardur's ETB effect, etc.).
    # (~23 commanders: Thantis, Zurgo, Toski, Ruric Thar, Haktos, Fumiko, etc.)
    ("forced_attack",
     "Forced attack each combat",
     re.compile(r"attacks? each combat if able|all creatures attack each combat", re.I),
     0.8),

    # Cascade / discover — commanders that grant cascade to spells you cast, or
    # that care about cascading / discovering.  Producers: other spells with
    # cascade, discover payoffs, and ways to cheat on mana cost.
    # Covers the keyword directly ("cascade", "discover") and commander text
    # that grants it ("spells you cast have cascade", "spells you cast this
    # turn have cascade").
    # (~6 commanders: Yidris, Abaddon, Averna, Maelstrom Wanderer, etc.)
    ("cascade",
     "Cascade / discover",
     re.compile(r"\bcascade\b|\bdiscover\b", re.I),
     0.9),
]

# ── Trigger clause extraction ─────────────────────────────────────────────────

_TRIGGER_START = re.compile(
    r"^(?:when(?:ever)?|if|each(?:\s+time)?)\b",
    re.I,
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _extract_trigger_clauses(oracle_text: str) -> list[str]:
    """Split oracle text into sentences and return those beginning with a trigger word."""
    if not oracle_text:
        return []
    # Flatten multi-ability text (blocks separated by \n\n) then split on sentences.
    flat = oracle_text.replace("\n\n", " ").replace("\n", " ")
    sentences = _SENTENCE_SPLIT.split(flat)
    return [s.strip() for s in sentences if s.strip() and _TRIGGER_START.match(s.strip())]


def _unmatched_triggers(oracle_text: str, oracle_signals: list[dict]) -> list[str]:
    """Return trigger clauses that no oracle pattern captured."""
    clauses = _extract_trigger_clauses(oracle_text)
    if not clauses:
        return []
    matched_phrases = [
        s["matched_phrase"].lower()
        for s in oracle_signals
        if s.get("source") == "oracle_text" and s.get("matched_phrase")
    ]
    unmatched = []
    for clause in clauses:
        clause_lower = clause.lower()
        if not any(phrase in clause_lower for phrase in matched_phrases):
            unmatched.append(clause)
    return unmatched


# ── Oracle text signal detection ──────────────────────────────────────────────

def _oracle_signals(oracle_text: str) -> list[dict]:
    """Run all ORACLE_PATTERNS against oracle_text and return matched signals."""
    if not oracle_text:
        return []
    seen: set[str] = set()
    signals: list[dict] = []
    for pattern_key, label, regex, score in ORACLE_PATTERNS:
        if pattern_key in seen:
            continue
        m = regex.search(oracle_text)
        if m:
            seen.add(pattern_key)
            signals.append({
                "pattern_key": pattern_key,
                "label": label,
                "source": "oracle_text",
                "matched_phrase": m.group(0).strip(),
                "score": score,
            })
    return signals


# ── XMage signal detection ────────────────────────────────────────────────────

def _xmage_signals(
    ability_classes: list[str],
    effect_classes: list[str],
    trigger_event_overrides: dict[str, str],
) -> list[dict]:
    """Translate XMage ability + effect classes into signals.

    Each recognised ability class maps to one signal.  The dominant effect
    class (first one that maps in EFFECT_CLASS_TO_EFFECT) is attached to every
    signal from this card — it captures what the trigger *does* (draw, damage,
    create_token, etc.), which breaks ties between commanders sharing the same
    trigger event but wanting different things from the 99.
    """
    dominant_effect = next(
        (EFFECT_CLASS_TO_EFFECT[ec] for ec in effect_classes if ec in EFFECT_CLASS_TO_EFFECT),
        None,
    )
    seen_events: set[str] = set()
    signals: list[dict] = []
    for ac in ability_classes:
        trigger_event = trigger_event_overrides.get(ac) or ABILITY_CLASS_TO_EVENT.get(ac)
        if not trigger_event:
            continue
        if trigger_event in seen_events:
            continue
        seen_events.add(trigger_event)
        signals.append({
            "pattern_key": trigger_event,
            "label": f"{trigger_event.replace('_', ' ').title()} (XMage)",
            "source": "xmage",
            "ability_class": ac,
            "effect_class": dominant_effect,
            "score": 0.85,
        })
    return signals


# ── XMage file index ──────────────────────────────────────────────────────────

def _build_xmage_index(xmage_dir: Path) -> dict[str, Path]:
    """Return {normalized_card_name: java_path} for all XMage card files."""
    cards_dir = xmage_dir / "Mage.Sets" / "src" / "mage" / "cards"
    if not cards_dir.exists():
        log.warning("XMage cards directory not found: %s — running without XMage data", cards_dir)
        return {}
    index: dict[str, Path] = {}
    for java_file in cards_dir.rglob("*.java"):
        index[_normalize(java_file.stem)] = java_file
    log.info("XMage index: %d card files in %s", len(index), cards_dir)
    return index


# ── DB helpers ────────────────────────────────────────────────────────────────

_COMMANDER_QUERY = """
    SELECT
        id::text,
        name,
        oracle_text,
        color_identity,
        cmc,
        type_line,
        keywords
    FROM cards
    WHERE legalities->>'commander' = 'legal'
      AND (
          type_line ILIKE '%Legendary Creature%'
          OR type_line ILIKE '%Legendary Planeswalker%'
          OR oracle_text ILIKE '%can be your commander%'
      )
    ORDER BY name
"""


def _load_commanders(conn) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(_COMMANDER_QUERY)
        return [dict(row) for row in cur.fetchall()]


# ── Main decomposition logic ──────────────────────────────────────────────────

def decompose(
    commanders: list[dict],
    xmage_index: dict[str, Path],
) -> list[dict]:
    """Decompose each commander into signals from both sources."""
    entries: list[dict] = []

    for cmd in commanders:
        oracle_text = cmd.get("oracle_text") or ""
        keywords = cmd.get("keywords") or []

        # Include keywords in oracle text for pattern matching
        # (some abilities are expressed only as keywords, e.g. "Landfall")
        combined_text = oracle_text
        if keywords:
            combined_text = oracle_text + "\n" + " ".join(keywords)

        # Oracle text signals
        o_signals = _oracle_signals(combined_text)

        # XMage signals
        norm_name = _normalize(cmd["name"])
        xmage_path = xmage_index.get(norm_name)
        x_signals: list[dict] = []
        xmage_file: str | None = None

        if xmage_path is not None:
            xmage_file = xmage_path.name
            try:
                ability_classes, effect_classes, overrides = parse_java_file(xmage_path)
                x_signals = _xmage_signals(ability_classes, effect_classes, overrides)
            except Exception as exc:
                log.warning("  parse error %s: %s", xmage_path.name, exc)

        # Unmatched triggers (oracle text clauses not captured by any oracle pattern)
        unmatched = _unmatched_triggers(oracle_text, o_signals)

        entries.append({
            "id":                cmd["id"],
            "name":              cmd["name"],
            "oracle_text":       oracle_text,
            "color_identity":    list(cmd.get("color_identity") or []),
            "cmc":               float(cmd.get("cmc") or 0),
            "type_line":         cmd.get("type_line") or "",
            "xmage_file":        xmage_file,
            "signals":           o_signals + x_signals,
            "unmatched_triggers": unmatched,
        })

    return entries


# ── Coverage report ───────────────────────────────────────────────────────────

def _coverage_report(entries: list[dict]) -> None:
    total = len(entries)
    xmage_found   = sum(1 for e in entries if e["xmage_file"])
    signal_counts = Counter(len(e["signals"]) for e in entries)
    oracle_only   = sum(1 for e in entries
                        if any(s["source"] == "oracle_text" for s in e["signals"])
                        and not any(s["source"] == "xmage" for s in e["signals"]))
    xmage_only    = sum(1 for e in entries
                        if any(s["source"] == "xmage" for s in e["signals"])
                        and not any(s["source"] == "oracle_text" for s in e["signals"]))
    both          = sum(1 for e in entries
                        if any(s["source"] == "oracle_text" for s in e["signals"])
                        and any(s["source"] == "xmage" for s in e["signals"]))
    no_signals    = sum(1 for e in entries if not e["signals"])

    # Top unmatched trigger phrases
    all_unmatched: list[str] = []
    for e in entries:
        all_unmatched.extend(e["unmatched_triggers"])
    # Normalise: lowercase, strip, truncate to 80 chars for grouping
    phrase_counter: Counter = Counter()
    for phrase in all_unmatched:
        key = phrase.lower().strip()[:80]
        phrase_counter[key] += 1

    log.info("─" * 60)
    log.info("Commander decomposition complete")
    log.info("─" * 60)
    log.info("Total commanders: %d", total)
    log.info("  XMage file found:  %d (%.1f%%)", xmage_found, 100 * xmage_found / max(total, 1))
    log.info("  No XMage file:     %d (%.1f%%)", total - xmage_found,
             100 * (total - xmage_found) / max(total, 1))
    log.info("")
    log.info("Signal coverage:")
    for n in sorted(signal_counts):
        label = f"{n} signal{'s' if n != 1 else ''}"
        if n >= 3:
            label = "3+ signals"
            break
        count = signal_counts[n]
        log.info("  %-15s %d (%.1f%%)", label, count, 100 * count / max(total, 1))
    three_plus = sum(v for k, v in signal_counts.items() if k >= 3)
    log.info("  %-15s %d (%.1f%%)", "3+ signals", three_plus, 100 * three_plus / max(total, 1))
    log.info("")
    log.info("Source breakdown:")
    log.info("  oracle text only:  %d (%.1f%%)", oracle_only, 100 * oracle_only / max(total, 1))
    log.info("  xmage only:        %d (%.1f%%)", xmage_only, 100 * xmage_only / max(total, 1))
    log.info("  both sources:      %d (%.1f%%)", both, 100 * both / max(total, 1))
    log.info("  no signals:        %d (%.1f%%)", no_signals, 100 * no_signals / max(total, 1))
    log.info("")
    if phrase_counter:
        log.info("Top unmatched trigger phrases (by frequency):")
        for phrase, count in phrase_counter.most_common(15):
            log.info("  [%3d]  %s", count, phrase)
    log.info("─" * 60)


# ── DB write: card_abilities ──────────────────────────────────────────────────

def _write_card_abilities(entries: list[dict]) -> None:
    """Upsert card_abilities rows for each commander signal.

    Uses ``trigger_event = pattern_key`` so that ``compute_synergy`` in
    ``pipeline.py`` can cross-join these commanders against the producer SQL
    in ``synergy/commander_mechanics.py:PATTERN_KEY_TO_PRODUCER_SQL``
    without any additional pattern duplication.

    Deduplicates by (card_id, pattern_key) — oracle_text signals take
    precedence over xmage for the same key.  ``source = 'decompose'``
    distinguishes these rows from ``tag_abilities`` ('pattern') and
    ``tag_abilities_xmage`` ('xmage').
    """
    # Deduplicate: one row per (card_id, pattern_key); prefer oracle_text
    rows: dict[tuple[str, str], dict] = {}
    for entry in entries:
        card_id = entry["id"]
        for sig in entry.get("signals", []):
            key = (card_id, sig["pattern_key"])
            existing = rows.get(key)
            if existing is None or sig["source"] == "oracle_text":
                rows[key] = {
                    "card_id":      card_id,
                    "ability_name": sig["label"],
                    "trigger_event": sig["pattern_key"],
                    "raw_text":     sig.get("matched_phrase") or sig.get("ability_class") or "",
                }

    if not rows:
        log.info("No signals to write to card_abilities.")
        return

    inserts = list(rows.values())
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO card_abilities
                    (card_id, ability_type, ability_name, trigger_event, source, raw_text)
                VALUES
                    (%(card_id)s::uuid, 'triggered', %(ability_name)s,
                     %(trigger_event)s, 'decompose', %(raw_text)s)
                ON CONFLICT (card_id, ability_type, ability_name, COALESCE(effect_class, ''))
                DO UPDATE SET
                    trigger_event = EXCLUDED.trigger_event,
                    raw_text      = EXCLUDED.raw_text,
                    source        = EXCLUDED.source
            """, inserts)
        conn.commit()
        log.info("Upserted %d card_abilities rows (source='decompose')", len(inserts))
    finally:
        conn.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Decompose all legal commanders into structured synergy signals."
    )
    parser.add_argument(
        "--xmage-dir",
        type=Path,
        default=Path(os.environ.get("XMAGE_DIR", "/mage")),
        help="XMage repository root (default: /mage or $XMAGE_DIR)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("/data/commander_decomposition.json"),
        help="Output JSON path (default: /data/commander_decomposition.json)",
    )
    parser.add_argument(
        "--no-xmage",
        action="store_true",
        help="Skip XMage lookup (oracle text patterns only)",
    )
    args = parser.parse_args()

    if not DATABASE_URL:
        sys.exit("DATABASE_URL environment variable is required.")

    # Build XMage index
    xmage_index: dict[str, Path] = {}
    if not args.no_xmage:
        xmage_index = _build_xmage_index(args.xmage_dir)

    # Load commanders from DB
    log.info("Loading commanders from DB…")
    conn = psycopg2.connect(DATABASE_URL)
    try:
        commanders = _load_commanders(conn)
    finally:
        conn.close()
    log.info("Found %d legal commanders", len(commanders))

    # Decompose
    log.info("Decomposing commanders…")
    entries = decompose(commanders, xmage_index)

    # Write output
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        json.dump(entries, fh, indent=2, ensure_ascii=False)
    log.info("Written: %s (%d entries)", args.out, len(entries))

    # Upsert card_abilities rows so compute_synergy can build producer→commander edges
    _write_card_abilities(entries)

    # Coverage report
    _coverage_report(entries)


if __name__ == "__main__":
    main()
