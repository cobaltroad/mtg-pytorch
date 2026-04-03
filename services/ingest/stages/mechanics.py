"""Tag candidate cards with deck-key role labels.

Three insertion phases, all ``ability_type='role'``:

1. **Coarse** — one row per deck key per matching card.
   ``source='oracle_text'`` or ``source='card_characteristic'`` depending on
   whether the SQL pattern queries oracle_text or card properties (type_line,
   toughness, etc.).

2. **Fine-grained** — one row per sub-pattern per matching card.
   Always ``source='oracle_text'``.  Gives Atraxa rows for both
   ``counter_trigger`` (coarse) and ``proliferate``, ``hardened_scales``, etc.

3. **Oracle-pattern** — ``ORACLE_PATTERNS`` from ``stages/decompose.py`` applied
   to every card in the database (not just legal commanders).  Always
   ``source='oracle_text'``.  Elvish Warmaster gets ``tribal_elf``,
   ``token_generator``, ``creature_token_generator``, etc.

Usage
-----
    # Tag all cards (idempotent):
    docker compose run --rm ingest python pipeline.py --stage tag_mechanics

    # Re-tag after updating WHERE clauses:
    docker compose run --rm ingest python pipeline.py --stage tag_mechanics --rescan

    # Or run directly:
    docker compose run --rm ingest python -m stages.mechanics [--rescan]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).parent.parent))

from synergy.commander_mechanics import (
    DECK_KEY_LABELS,
    PATTERN_KEY_TO_CONSUMER_SQL,
    PATTERN_KEY_TO_PRODUCER_SQL,
)
from synergy.spell import PATTERNS as _spell_patterns
from synergy.staples.treasure import SQL as _TREASURE_SQL
from synergy.staples.token import SQL as _TOKEN_SQL
from mtg_sql.staples.removal import DESTROY as _DESTROY, DAMAGE as _DAMAGE
from synergy.triggered_ability.cast import CAST_SQL as _CAST_SQL
from synergy.triggered_ability.lifegain import (
    LIFEGAIN_SQL as _LIFEGAIN_SQL,
    SQL as _LIFEGAIN_COARSE,
)
from synergy.triggered_ability.draw import (
    DRAW_SQL as _DRAW_SQL,
    SQL as _DRAW_COARSE,
)
from synergy.triggered_ability.counter import (
    COUNTER_SQL as _COUNTER_SQL,
    SQL as _COUNTER_COARSE,
)
from synergy.triggered_ability.creature_etb import (
    CREATURE_ETB_SQL as _CREATURE_ETB_SQL,
    SQL as _CREATURE_ETB_COARSE,
)
from synergy.triggered_ability.sacrifice import (
    SACRIFICE_SQL as _SACRIFICE_SQL,
    SQL as _SACRIFICE_COARSE,
)
from synergy.activated_ability.sac_outlet import (
    SAC_OUTLET_SQL as _SAC_OUTLET_SQL,
    SQL as _SAC_OUTLET_COARSE,
)
from synergy.activated_ability.mana_producer import (
    MANA_PRODUCER_SQL as _MANA_PRODUCER_SQL,
    SQL as _MANA_COARSE,
)
from synergy.combat.combat import (
    COMBAT_SQL as _COMBAT_SQL,
    SQL as _COMBAT_COARSE,
)
from stages.decompose import ORACLE_PATTERNS as _ORACLE_PATTERNS, _detect as _decompose_detect

DATABASE_URL = os.environ.get("DATABASE_URL", "").replace(
    "postgresql+asyncpg://", "postgresql://"
)

log = logging.getLogger(__name__)

# ── DECK_KEY_TO_SQL ────────────────────────────────────────────────────────────
# Maps every deck key in DECK_KEY_LABELS to a SQL WHERE fragment that selects
# matching cards from the ``cards`` table.  Built from three layers:
#
#   1. PATTERN_KEY_TO_PRODUCER_SQL — producer deck keys from commander_mechanics.py
#   2. PATTERN_KEY_TO_CONSUMER_SQL — consumer entries whose key IS in
#      DECK_KEY_LABELS (tribal_*, cast_trigger_{color}); these already use
#      direct SQL (tribal_sql, _spells) so no override needed
#   3. Direct SQL constants — imported from synergy sub-modules; override any
#      family_sql() entries from layers 1 and 2 so that this stage is fully
#      self-contained and does not depend on card_abilities rows from tag_abilities
#
# Layer 3 wins over layer 2, which wins over layer 1.

DECK_KEY_TO_SQL: dict[str, str] = {
    # ── 1. Producer deck keys ─────────────────────────────────────────────────
    **PATTERN_KEY_TO_PRODUCER_SQL,

    # ── 2. Consumer entries whose key is a deck key ───────────────────────────
    **{
        k: v
        for k, v in PATTERN_KEY_TO_CONSUMER_SQL.items()
        if k in DECK_KEY_LABELS
    },

    # ── 3. Direct oracle_text SQL — overrides any family_sql() from sections 1/2 ─
    # All entries here query the cards table directly (oracle_text, type_line,
    # toughness, cmc) with no dependency on card_abilities rows from tag_abilities.
    # This makes tag_mechanics self-contained and runnable standalone.

    # Producer: counter-placement commanders → counter amplifiers (proliferate, doublers)
    "counter_trigger":       _COUNTER_COARSE,

    # Producer: lifegain commanders → lifegain payoffs
    "lifegain_trigger":      _LIFEGAIN_COARSE,

    # Producer: draw commanders → draw payoffs
    "draw_trigger":          _DRAW_COARSE,

    # Producer: creature token generators → ETB payoffs (Purphoros, Impact Tremors)
    "creature_etb_payoff":   _CREATURE_ETB_COARSE,

    # Producer: attack/combat commanders → evasion grants, pump, keywords
    "combat_tricks":         _COMBAT_COARSE,

    # Producer: death-trigger / sacrifice commanders → sac outlets + fodder
    "sac_outlet":            _SAC_OUTLET_COARSE,
    "sacrifice_fodder":      _SACRIFICE_COARSE,

    # Removal modes (death-trigger commanders want DESTROY + DAMAGE; the
    # labels in DECK_KEY_LABELS match these specific modes, not generic removal)
    "destroy_removal":       _DESTROY,
    "damage_removal":        _DAMAGE,

    # Fodder for sacrifice-payoff commanders
    "treasure_generators":   _TREASURE_SQL,
    "token_generators":      _TOKEN_SQL,

    # Death-trigger fodder: creatures that die to any pinger or sac outlet
    "toughness_1_creatures": _spell_patterns["toughness_1"],

    # Spell fodder — cards of the type that trigger the commander's cast trigger
    "spell_enchantment":     _spell_patterns["spell_enchantment"],
    "spell_creature":        _spell_patterns["spell_creature"],
    "spell_artifact":        _spell_patterns["spell_artifact"],
    "spell_instant_sorcery": _spell_patterns["spell_instant_sorcery"],
    "spell_historic":        _spell_patterns["spell_historic"],
    "spell_aura_equipment":  _spell_patterns["spell_aura_equipment"],
    # Color spell fodder — for color-based cast-trigger commanders (K'rrik, Aragorn, etc.)
    **{f"spell_{c}": _spell_patterns[f"spell_{c}"]
       for c in ("white", "blue", "black", "red", "green", "colorless")},

    # Cast-trigger amplifiers — cards with the *same* trigger as the commander
    **_CAST_SQL,

    # Consumer: mana-dork — creatures that tap for mana
    "mana_dork":             f"type_line ILIKE '%%Creature%%' AND {_MANA_COARSE}",
}


# ── Source categorisation ──────────────────────────────────────────────────────
# 'card_characteristic' — evidence comes from type_line, toughness, CMC, color, etc.
# 'oracle_text'         — evidence comes from the card's rules text.
# Any key not listed here defaults to 'oracle_text'.
_CARD_CHAR_KEYS: frozenset[str] = frozenset({
    "spell_enchantment", "spell_creature", "spell_artifact",
    "spell_instant_sorcery", "spell_historic", "spell_aura_equipment",
    "spell_white", "spell_blue", "spell_black", "spell_red", "spell_green", "spell_colorless",
    "toughness_1_creatures",
} | {k for k in DECK_KEY_TO_SQL if k.startswith("tribal_")})


def _src(key: str) -> str:
    return "card_characteristic" if key in _CARD_CHAR_KEYS else "oracle_text"


# ── Sub-pattern labels and fine-grained SQL ───────────────────────────────────
from synergy.triggered_ability.counter import PATTERNS as _counter_patterns
from synergy.triggered_ability.lifegain import PATTERNS as _lifegain_patterns
from synergy.triggered_ability.draw import PATTERNS as _draw_patterns
from synergy.triggered_ability.creature_etb import PATTERNS as _etb_patterns
from synergy.triggered_ability.sacrifice import PATTERNS as _sacrifice_patterns
from synergy.activated_ability.sac_outlet import PATTERNS as _sac_outlet_patterns
from synergy.activated_ability.mana_producer import PATTERNS as _mana_producer_patterns
from synergy.combat.combat import PATTERNS as _combat_patterns

_FINE_KEY_LABELS: dict[str, str] = {
    key: label
    for patterns in [
        _counter_patterns, _lifegain_patterns, _draw_patterns,
        _etb_patterns, _sacrifice_patterns, _sac_outlet_patterns,
        _mana_producer_patterns, _combat_patterns,
    ]
    for key, label, _ in patterns
}

# All fine-grained keys are oracle_text evidence.
# Mana sub-keys get a Creature type_line filter so only creatures are tagged
# (mana_dork keys, not mana rocks which are handled by the coarse mana_dork entry).
FINE_KEY_TO_SQL: dict[str, str] = {
    **_COUNTER_SQL,
    **_LIFEGAIN_SQL,
    **_DRAW_SQL,
    **_CREATURE_ETB_SQL,
    **_SACRIFICE_SQL,
    **_SAC_OUTLET_SQL,
    **{k: f"type_line ILIKE '%%Creature%%' AND {v}" for k, v in _MANA_PRODUCER_SQL.items()},
    **_COMBAT_SQL,
    # cast keys are self-referential (coarse IS fine); already in DECK_KEY_TO_SQL.
}


# ── Stage function ─────────────────────────────────────────────────────────────

_INSERT = """
    INSERT INTO card_abilities
        (card_id, ability_type, ability_name, trigger_event,
         effect_class, raw_text, source)
    SELECT id, 'role', %(label)s, %(deck_key)s, NULL, NULL, %(source)s
    FROM cards
    WHERE {where_sql}
    ON CONFLICT (card_id, ability_type, ability_name, COALESCE(effect_class, ''))
    DO NOTHING
"""

_ORACLE_INSERT = """
    INSERT INTO card_abilities
        (card_id, ability_type, ability_name, trigger_event,
         effect_class, raw_text, source)
    VALUES (%(card_id)s::uuid, 'role', %(ability_name)s, %(trigger_event)s,
            NULL, NULL, 'oracle_text')
    ON CONFLICT (card_id, ability_type, ability_name, COALESCE(effect_class, ''))
    DO NOTHING
"""


def tag_mechanics(rescan: bool = False) -> None:
    """Write role-typed deck-key tags to card_abilities.

    Three phases:
    - Coarse: one row per deck key per matching card.
      source='oracle_text' or 'card_characteristic'.
    - Fine-grained: one row per sub-pattern per matching card.
      source='oracle_text'.
    - Oracle-pattern: ORACLE_PATTERNS applied to all cards.
      source='oracle_text'.

    Parameters
    ----------
    rescan:
        When *True*, delete all existing ``source IN ('oracle_text',
        'card_characteristic')`` rows first so every key is re-evaluated
        from scratch.  Use after changing a WHERE clause or pattern.
    """
    if not DATABASE_URL:
        sys.exit("DATABASE_URL environment variable is required.")

    conn = psycopg2.connect(DATABASE_URL)
    try:
        if rescan:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM card_abilities"
                    " WHERE source IN ('oracle_text', 'card_characteristic')"
                )
                deleted = cur.rowcount
            conn.commit()
            log.info("Rescan: deleted %d existing rows", deleted)

        # ── Phase 1: Coarse deck-key rows ─────────────────────────────────────
        total = 0
        skipped = 0
        for deck_key, where_sql in DECK_KEY_TO_SQL.items():
            label = DECK_KEY_LABELS.get(deck_key)
            if not label:
                log.debug("Skipping %s — not in DECK_KEY_LABELS", deck_key)
                skipped += 1
                continue

            sql = _INSERT.format(where_sql=where_sql)
            with conn.cursor() as cur:
                cur.execute(sql, {"label": label, "deck_key": deck_key, "source": _src(deck_key)})
                n = cur.rowcount
            conn.commit()
            total += n
            log.info("  %-32s  %5d rows  (%s)", deck_key, n, label)

        log.info("  Phase 1 complete: %d coarse rows (%d keys skipped)", total, skipped)

        # ── Phase 2: Fine-grained sub-pattern rows ────────────────────────────
        fine_total = 0
        for sub_key, where_sql in FINE_KEY_TO_SQL.items():
            label = _FINE_KEY_LABELS.get(sub_key)
            if not label:
                log.debug("Skipping fine key %s — no label found", sub_key)
                continue
            sql = _INSERT.format(where_sql=where_sql)
            with conn.cursor() as cur:
                cur.execute(sql, {"label": label, "deck_key": sub_key, "source": "oracle_text"})
                n = cur.rowcount
            conn.commit()
            fine_total += n
            log.info("  [fine] %-32s  %5d rows  (%s)", sub_key, n, label)

        log.info("  Phase 2 complete: %d fine-grained rows", fine_total)

        # ── Phase 3: Oracle-pattern rows (all cards) ──────────────────────────
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                "SELECT id::text, oracle_text, type_line FROM cards"
                " WHERE oracle_text IS NOT NULL"
            )
            all_cards = cur.fetchall()

        oracle_rows: list[dict] = []
        for row in all_cards:
            for key, label, _phrase in _decompose_detect(
                row["oracle_text"] or "", row["type_line"] or ""
            ):
                oracle_rows.append({
                    "card_id": row["id"],
                    "ability_name": label,
                    "trigger_event": key,
                })

        if oracle_rows:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(cur, _ORACLE_INSERT, oracle_rows, page_size=500)
            oracle_total = len(oracle_rows)
            conn.commit()
        else:
            oracle_total = 0

        log.info("  Phase 3 complete: %d oracle-pattern rows (%d cards scanned)",
                 oracle_total, len(all_cards))

        log.info(
            "tag_mechanics complete: %d coarse + %d fine + %d oracle-pattern rows inserted",
            total, fine_total, oracle_total,
        )
    finally:
        conn.close()


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import logging as _logging

    _logging.basicConfig(
        level=_logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    parser = argparse.ArgumentParser(
        description="Tag candidate cards with deck-key role labels."
    )
    parser.add_argument(
        "--rescan",
        action="store_true",
        help=(
            "Delete all existing source='oracle_text'/'card_characteristic' rows first, "
            "then re-insert.  Use after updating WHERE clauses or patterns."
        ),
    )
    args = parser.parse_args()
    tag_mechanics(rescan=args.rescan)
