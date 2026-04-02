"""Tag candidate cards with high-level deck-key role labels.

For every deck key in ``DECK_KEY_LABELS`` (``synergy/commander_mechanics.py``),
this stage runs the corresponding SQL WHERE fragment against the ``cards`` table
and inserts ``card_abilities`` rows with::

    ability_type = 'role'
    source       = 'mechanic'
    trigger_event = <deck_key>          e.g. 'tribal_elf', 'mana_dork', 'counter_trigger'
    ability_name  = <human label>       e.g. 'Elf tribal creatures', 'Mana ability creatures'

The candidates endpoint in the API already queries ``ability_type = 'role'``,
so these tags surface in the UI scoring table with no API changes.

This closes the gap between a commander's decompose signals (what the deck
needs) and the tags shown on candidate cards — e.g. Tyvar the Bellicose fires
decompose keys ``tribal_elf``, ``mana_dork``, and ``counter_trigger``, and after
this stage, candidate cards display those same labels as role tags.

Usage
-----
    # Tag all cards (idempotent):
    docker compose run --rm ingest python pipeline.py --stage tag_mechanic_tags

    # Re-tag after updating WHERE clauses in commander_mechanics.py:
    docker compose run --rm ingest python pipeline.py --stage tag_mechanic_tags --rescan

    # Or run directly:
    docker compose run --rm ingest python -m stages.mechanic_tags [--rescan]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import psycopg2

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
from synergy.triggered_ability.lifegain import SQL as _LIFEGAIN_SQL
from synergy.triggered_ability.draw import SQL as _DRAW_SQL
from synergy.triggered_ability.counter import SQL as _COUNTER_SQL
from synergy.triggered_ability.creature_etb import SQL as _CREATURE_ETB_SQL
from synergy.triggered_ability.sacrifice import SQL as _SACRIFICE_SQL
from synergy.activated_ability.sac_outlet import SQL as _SAC_OUTLET_SQL
from synergy.activated_ability.mana_producer import SQL as _MANA_PRODUCER_SQL
from synergy.combat.combat import SQL as _COMBAT_SQL

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
    # This makes tag_mechanic_tags self-contained and runnable standalone.

    # Producer: counter-placement commanders → counter amplifiers (proliferate, doublers)
    "counter_trigger":       _COUNTER_SQL,

    # Producer: lifegain commanders → lifegain payoffs
    "lifegain_trigger":      _LIFEGAIN_SQL,

    # Producer: draw commanders → draw payoffs
    "draw_trigger":          _DRAW_SQL,

    # Producer: creature token generators → ETB payoffs (Purphoros, Impact Tremors)
    "creature_etb_payoff":   _CREATURE_ETB_SQL,

    # Producer: attack/combat commanders → evasion grants, pump, keywords
    "combat_tricks":         _COMBAT_SQL,

    # Producer: death-trigger / sacrifice commanders → sac outlets + fodder
    "sac_outlet":            _SAC_OUTLET_SQL,
    "sacrifice_fodder":      _SACRIFICE_SQL,

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

    # Cast-trigger amplifiers — cards with the *same* trigger as the commander
    **_CAST_SQL,

    # Consumer: mana-dork — creatures that tap for mana
    "mana_dork":             f"type_line ILIKE '%%Creature%%' AND {_MANA_PRODUCER_SQL}",
}


# ── Stage function ─────────────────────────────────────────────────────────────

_INSERT = """
    INSERT INTO card_abilities
        (card_id, ability_type, ability_name, trigger_event,
         effect_class, raw_text, source)
    SELECT id, 'role', %(label)s, %(deck_key)s, NULL, NULL, 'mechanic'
    FROM cards
    WHERE {where_sql}
    ON CONFLICT (card_id, ability_type, ability_name, COALESCE(effect_class, ''))
    DO NOTHING
"""


def tag_mechanic_tags(rescan: bool = False) -> None:
    """Write role-typed deck-key tags to card_abilities (source='mechanic').

    Parameters
    ----------
    rescan:
        When *True*, delete all existing ``source='mechanic'`` rows first so
        every deck key is re-evaluated from scratch.  Use after changing a WHERE
        clause in ``commander_mechanics.py`` or any of the SQL building blocks.
    """
    if not DATABASE_URL:
        sys.exit("DATABASE_URL environment variable is required.")

    conn = psycopg2.connect(DATABASE_URL)
    try:
        if rescan:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM card_abilities"
                    " WHERE source = 'mechanic' AND ability_type = 'role'"
                )
                deleted = cur.rowcount
            conn.commit()
            log.info("Rescan: deleted %d existing mechanic role rows", deleted)

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
                cur.execute(sql, {"label": label, "deck_key": deck_key})
                n = cur.rowcount
            conn.commit()
            total += n
            log.info("  %-32s  %5d rows  (%s)", deck_key, n, label)

        log.info(
            "tag_mechanic_tags complete: %d total rows inserted (%d keys skipped)",
            total,
            skipped,
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
        description="Tag candidate cards with deck-key role labels (source='mechanic')."
    )
    parser.add_argument(
        "--rescan",
        action="store_true",
        help=(
            "Delete all existing source='mechanic' role rows first, then re-insert. "
            "Use after updating WHERE clauses in commander_mechanics.py."
        ),
    )
    args = parser.parse_args()
    tag_mechanic_tags(rescan=args.rescan)
