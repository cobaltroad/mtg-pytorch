"""Shared pool-building helpers — DB rows → builder card dicts.

Used by both the ingest eval script (scripts/build_deck.py, psycopg2) and
the API build path (services/api/ops/composition.py, SQLAlchemy async).
Only row-shape logic lives here; each caller owns its own query execution.

The canonical SELECT column list is CARD_COLUMNS; rows are converted with
row_to_card().  Pool WHERE fragments come from shared/mtg_sql/staples and
carry '%%' escapes, which read identically under psycopg2 (no-param
execute) and asyncpg (Postgres treats '%%' as two LIKE wildcards).
"""

from __future__ import annotations

import json
import re

from mtg_sql.staples import (
    draw_engine,
    draw_spell,
    interaction,
    protection,
    removal,
    sweeper,
)
from mtg_sql.staples.ramp import SQL as _RAMP_SQL

#: Auto-includes resolved by name; land entries count against the land quota.
FORCED_NAMES = ("Sol Ring", "Arcane Signet", "Command Tower")

#: Quota name → WHERE fragment over cards.
POOL_SQL: dict[str, str] = {
    "ramp": _RAMP_SQL,
    "draw_engine": draw_engine.SQL,
    "draw_spell": draw_spell.SQL,
    "spot_removal": f"(({removal.SQL}) OR ({interaction.SQL}))",
    "sweeper": sweeper.SQL,
    "protection": protection.SQL,
}

#: Canonical column list — c = cards, f = card_facts.
CARD_COLUMNS = (
    "c.id::text AS id, c.name, c.cmc, c.mana_cost, c.color_identity,"
    " c.produced_mana, c.type_line, c.oracle_text,"
    " f.pips, f.hybrid_pips, f.is_land, f.is_basic, f.etb_tapped, f.is_fetch"
)

#: Filter appended to every pool query: no-cost nonland cards (Evermind,
#: suspend-only spells) are uncastable by normal means.
CASTABLE_FILTER = "(c.mana_cost IS NOT NULL OR f.is_land)"

# Largest "Add {…}{…}" clause → mana per activation (Sol Ring 2, Thran
# Dynamo 3).  The goldfisher counts each source once per turn, so this is
# what makes big-mana commanders castable in simulation.
_ADD_CLAUSE_RE = re.compile(r"add ((?:\{[WUBRGCS0-9]\})+)", re.IGNORECASE)
_ADD_SYMBOL_RE = re.compile(r"\{[WUBRGCS0-9]\}")

#: Commander discounts its own cost (Karador) — the goldfisher can't model
#: it, so callers relax the castability gate by GATE_RELAX_COST_REDUCTION.
COST_REDUCTION_RE = re.compile(r"costs? \{?[X0-9]*\}? ?less to cast", re.IGNORECASE)
GATE_RELAX_COST_REDUCTION = 0.15


def mana_output(oracle_text: str | None) -> int:
    best = 1
    for clause in _ADD_CLAUSE_RE.findall(oracle_text or ""):
        best = max(best, len(_ADD_SYMBOL_RE.findall(clause)))
    return best


def _jsonish(value, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def row_to_card(row, roles: set[str]) -> dict:
    """Convert a CARD_COLUMNS row (mapping-like) into a builder card dict."""
    return {
        "id": row["id"],
        "name": row["name"],
        "mv": int(row["cmc"] or 0),
        "mana_cost": row["mana_cost"],
        "type_line": row["type_line"],
        "pips": _jsonish(row["pips"], {}),
        "hybrid": _jsonish(row["hybrid_pips"], []),
        "is_land": row["is_land"],
        "is_basic": row["is_basic"],
        "produces": [c for c in (row["produced_mana"] or []) if c in "WUBRGC"],
        "mana_output": mana_output(row["oracle_text"]),
        "etb_tapped": row["etb_tapped"],
        "is_fetch": row["is_fetch"],
        "roles": set(roles),
    }


def sort_pool(cards: list[dict], role: str) -> list[dict]:
    """Heuristic in-pool ranking (the baseline the model must beat).

    Ramp prefers raw mana output (Thran Dynamo over trinkets), then the
    2-MV signet tier; everything else cheap-first; name breaks ties.
    """
    if role == "ramp":
        cards.sort(key=lambda c: (-min(c["mana_output"], 3), abs(c["mv"] - 2), c["name"]))
    else:
        cards.sort(key=lambda c: (c["mv"], c["name"]))
    return cards
