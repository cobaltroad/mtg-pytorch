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
    wincon,
)
from mtg_sql.staples.ramp import SQL as _RAMP_SQL

#: Auto-includes resolved by name; land entries count against the land quota.
FORCED_NAMES = ("Sol Ring", "Arcane Signet", "Command Tower")

#: Quota name → WHERE fragment over cards.
POOL_SQL: dict[str, str] = {
    "ramp": _RAMP_SQL,
    "draw_engine": draw_engine.SQL,
    "draw_spell": draw_spell.SQL,
    # Counterspells only from interaction — its protection clauses belong
    # to the dedicated protection pool (#140).
    "spot_removal": f"(({removal.SQL}) OR ({interaction.COUNTERSPELLS}))",
    "sweeper": sweeper.SQL,
    "protection": protection.SQL,
    # Deliberate finishers for the wincon audit (#141) — not a quota; the
    # builder guarantees a win path among the spells.
    "wincon": wincon.SQL,
    # Drain/ping sources — counted for density in the win-path audit,
    # never drawn from as a pool.
    "drain": wincon.DRAIN,
}

#: Canonical column list — c = cards, f = card_facts.
CARD_COLUMNS = (
    "c.id::text AS id, c.name, c.cmc, c.mana_cost, c.color_identity,"
    " c.produced_mana, c.type_line, c.oracle_text, c.edhrec_rank, c.power,"
    " f.pips, f.hybrid_pips, f.is_land, f.is_basic, f.etb_tapped, f.is_fetch,"
    " f.is_mdfc_land"
)

#: Sort sentinel for cards MTGJSON has no EDHREC rank for (new/unplayed).
UNRANKED = 10**9

#: Land-pool WHERE fragment: real nonbasics plus spell-front modal DFCs with
#: a land face (Malakir Rebirth) — playable as land drops (#143).
LAND_POOL_FILTER = "((f.is_land AND NOT f.is_basic) OR f.is_mdfc_land)"

#: Filter appended to every pool query: no-cost nonland cards (Evermind,
#: suspend-only spells) are uncastable by normal means.
CASTABLE_FILTER = "(c.mana_cost IS NOT NULL OR f.is_land)"

# Largest "Add {…}{…}" clause → mana per activation (Sol Ring 2, Thran
# Dynamo 3).  The goldfisher counts each source once per turn, so this is
# what makes big-mana commanders castable in simulation.
_ADD_CLAUSE_RE = re.compile(r"add ((?:\{[WUBRGCS0-9]\})+)", re.IGNORECASE)
_ADD_SYMBOL_RE = re.compile(r"\{[WUBRGCS0-9]\}")

#: Commander discounts its own cost (Karador, Ghalta) — callers pass
#: cost_reduction=True to build_deck, which simulates a per-turn generic
#: discount in the goldfisher (#142).  The gate is never relaxed.
COST_REDUCTION_RE = re.compile(r"costs? \{?[X0-9]*\}? ?less to cast", re.IGNORECASE)


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


def _power(value) -> int:
    """Numeric power for win-path math; '*'-style powers count as 0."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def row_to_card(row, roles: set[str]) -> dict:
    """Convert a CARD_COLUMNS row (mapping-like) into a builder card dict."""
    return {
        "id": row["id"],
        "name": row["name"],
        "mv": int(row["cmc"] or 0),
        "power": _power(row["power"]),
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
        "is_mdfc_land": row["is_mdfc_land"],
        "edhrec_rank": row["edhrec_rank"] if row["edhrec_rank"] is not None else UNRANKED,
        "roles": set(roles),
    }


def sort_pool(cards: list[dict], role: str) -> list[dict]:
    """Heuristic in-pool ranking (the baseline the model must beat).

    Popularity prior (#140): EDHREC rank orders every pool — pool
    membership already constrains *function*, so popularity within the
    pool is "best-in-slot by table consensus".  Ramp keeps raw mana
    output as its primary key (castability physics — Thran Dynamo must
    outrank trinkets for big-mana commanders regardless of popularity).
    Unranked cards (UNRANKED sentinel) tail out; name breaks final ties.
    """
    if role == "ramp":
        cards.sort(key=lambda c: (-min(c["mana_output"], 3), c["edhrec_rank"], c["name"]))
    else:
        cards.sort(key=lambda c: (c["edhrec_rank"], c["mv"], c["name"]))
    return cards


def apply_vote_overrides(
    pools: dict[str, list[dict]],
    land_pool: list[dict],
    forced: list[dict],
    upvoted: set[str],
    downvoted: set[str],
) -> tuple[dict[str, list[dict]], list[dict], list[dict], set[str], set[str]]:
    """Per-build vote overrides — the amend pass (#184).

    Downvoted card ids vanish from every pool, the land pool, and the
    forced includes.  Upvoted ids move to the front of each pool they
    appear in and gain the 'pinned' role, which the builder's cut paths
    (feedback-loop land conversion, pip-offender swap, wincon-audit
    swap) respect.  Pins never bypass quotas or the castability gate —
    they only win the within-slot ranking they were already eligible
    for.  Lands are exempt from pinning (the builder re-ranks lands by
    land_quality; land votes aren't captured by the UI anyway).

    Returns (pools, land_pool, forced, pinned_ids, unplaced_ids) —
    unplaced_ids are upvoted cards found in no pool (pool SQL no longer
    claims them), surfaced as a build warning by the caller.
    """
    keep = lambda c: c["id"] not in downvoted  # noqa: E731
    out_pools: dict[str, list[dict]] = {}
    pinned: set[str] = set()
    for role, pool in pools.items():
        pool = [c for c in pool if keep(c)]
        if role != "drain":  # drain is a density audit, order-irrelevant
            front = [c for c in pool if c["id"] in upvoted]
            if front:
                for c in front:
                    c.setdefault("roles", set()).add("pinned")
                    pinned.add(c["id"])
                pool = front + [c for c in pool if c["id"] not in upvoted]
        out_pools[role] = pool
    return (
        out_pools,
        [c for c in land_pool if keep(c)],
        [c for c in forced if keep(c)],
        pinned,
        upvoted - pinned,
    )
