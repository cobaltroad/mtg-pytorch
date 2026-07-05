"""Composition-first deck building — API glue (plan W5).

Async counterpart of services/ingest/scripts/build_deck.py.  Pools come
from the shared staple SQL; decompose signals and the theme pool come from
DB-materialized data (card_abilities source='decompose' and
decomposed_candidates synergy edges), so this module has no dependency on
the ingest service's synergy package.

The pure engine (profile derivation, builder, goldfisher, model ranking)
lives in shared/composition; this file only fetches rows and saves the
finished deck JSON in the shape the Generated Decks viewer expects.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from composition.builder import BuildResult, build_deck
from composition.pool_helpers import (
    CARD_COLUMNS,
    CASTABLE_FILTER,
    COST_REDUCTION_RE,
    FORCED_NAMES,
    GATE_RELAX_COST_REDUCTION,
    POOL_SQL,
    row_to_card,
    sort_pool,
)
from composition.profile import CompositionProfile, derive_profile
from composition.ranking import load_ranker

log = logging.getLogger(__name__)

_FROM = "FROM cards c JOIN card_facts f ON f.card_id = c.id"


async def _query_pool(db: AsyncSession, where_sql: str, identity: frozenset, role: str) -> list[dict]:
    # Staple SQL fragments contain regex '(?:…)' — escape colons so text()
    # doesn't read them as bind parameters.
    where_safe = where_sql.replace(":", r"\:")
    result = await db.execute(
        text(f"SELECT {CARD_COLUMNS} {_FROM} WHERE ({where_safe}) AND {CASTABLE_FILTER}")
    )
    cards = [
        row_to_card(row, {role})
        for row in result.mappings()
        if frozenset(row["color_identity"] or []) <= identity
    ]
    return sort_pool(cards, role)


async def _theme_pool(db: AsyncSession, commander_id: str, identity: frozenset) -> list[dict]:
    """Theme candidates from materialized decomposed_candidates edges.

    Edge metadata carries pattern_keys → theme_keys for the builder's
    diminishing-returns counters.
    """
    result = await db.execute(
        text(
            f"SELECT {CARD_COLUMNS}, e.metadata AS edge_meta"
            f" {_FROM} JOIN synergy_edges e ON e.card_b = c.id"
            " WHERE e.card_a = CAST(:cmd AS uuid)"
            "   AND e.score_type = 'decomposed_candidates'"
            f"  AND {CASTABLE_FILTER}"
        ),
        {"cmd": commander_id},
    )
    hits: dict[str, dict] = {}
    for row in result.mappings():
        if not frozenset(row["color_identity"] or []) <= identity:
            continue
        if row["id"] == commander_id:
            continue
        meta = row["edge_meta"]
        if isinstance(meta, str):
            meta = json.loads(meta or "{}")
        keys = set((meta or {}).get("pattern_keys") or [])
        entry = hits.setdefault(row["id"], row_to_card(row, {"theme"}))
        entry.setdefault("theme_keys", set()).update(keys)
    pool = list(hits.values())
    pool.sort(key=lambda c: (-len(c.get("theme_keys", ())), c["mv"], c["name"]))
    return pool


async def _basics(db: AsyncSession) -> dict[str, dict]:
    subtype_to_color = {"Plains": "W", "Island": "U", "Swamp": "B",
                        "Mountain": "R", "Forest": "G"}
    out: dict[str, dict] = {}
    for subtype, color in subtype_to_color.items():
        result = await db.execute(
            text(f"SELECT {CARD_COLUMNS} {_FROM} WHERE c.type_line ILIKE :tl LIMIT 1"),
            {"tl": f"Basic Land — {subtype}"},
        )
        row = result.mappings().first()
        if row:
            out[color] = row_to_card(row, set())
    # Wastes has no land subtype — its type line is just "Basic Land".
    result = await db.execute(
        text(f"SELECT {CARD_COLUMNS} {_FROM} WHERE c.name = 'Wastes' LIMIT 1")
    )
    row = result.mappings().first()
    if row:
        out["C"] = row_to_card(row, set())
    return out


async def _forced(db: AsyncSession, identity: frozenset) -> list[dict]:
    out = []
    for name in FORCED_NAMES:
        result = await db.execute(
            text(f"SELECT {CARD_COLUMNS} {_FROM} WHERE c.name = :n LIMIT 1"), {"n": name}
        )
        row = result.mappings().first()
        if row and frozenset(row["color_identity"] or []) <= identity:
            out.append(row_to_card(row, {"ramp"} if not row["is_land"] else set()))
    return out


async def _embeddings(db: AsyncSession, card_ids: list[str]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for i in range(0, len(card_ids), 5000):
        result = await db.execute(
            text(
                "SELECT card_id::text, embedding::text FROM card_embeddings"
                " WHERE card_id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": card_ids[i : i + 5000]},
        )
        for cid, vec in result.fetchall():
            out[cid] = json.loads(vec)
    return out


async def _theme_density(db: AsyncSession, commander_id: str, theme_ids: list[str]) -> dict:
    if not theme_ids:
        return {"commander_edge_rate": 0.0, "pairwise_rate": 0.0}
    result = await db.execute(
        text(
            "SELECT count(DISTINCT CASE WHEN card_a = CAST(:cmd AS uuid) THEN card_b ELSE card_a END)"
            " FROM synergy_edges"
            " WHERE (card_a = CAST(:cmd AS uuid) AND card_b = ANY(CAST(:ids AS uuid[])))"
            "    OR (card_b = CAST(:cmd AS uuid) AND card_a = ANY(CAST(:ids AS uuid[])))"
        ),
        {"cmd": commander_id, "ids": theme_ids},
    )
    cmd_hits = result.scalar() or 0
    result = await db.execute(
        text(
            "SELECT count(*) FROM synergy_edges"
            " WHERE card_a = ANY(CAST(:ids AS uuid[])) AND card_b = ANY(CAST(:ids AS uuid[]))"
        ),
        {"ids": theme_ids},
    )
    pair_hits = result.scalar() or 0
    n = len(theme_ids)
    return {
        "commander_edge_rate": cmd_hits / n,
        "pairwise_rate": pair_hits / (n * (n - 1)) if n > 1 else 0.0,
    }


def _deck_json(
    profile: CompositionProfile,
    result: BuildResult,
    commander: dict,
    ranking: str,
    theme_density: dict,
) -> dict:
    """Deck JSON compatible with render_deck() in the UI, plus composition data."""
    slot_of: dict[str, str] = {}
    for slot, names in result.breakdown.items():
        for n in names:
            slot_of.setdefault(n, slot)

    counts: dict[str, int] = {}
    order: list[dict] = []
    for card in result.deck:
        if card["name"] in counts:
            counts[card["name"]] += 1
            continue
        counts[card["name"]] = 1
        order.append(card)

    cards_out = [
        {
            "name": c["name"],
            "count": counts[c["name"]],
            "type_line": c.get("type_line") or "",
            "mana_cost": c.get("mana_cost") or "",
            "cmc": c["mv"],
            "slot": slot_of.get(c["name"], "basic_land" if c["is_basic"] else "land"),
        }
        for c in order
    ]
    # Scores keep builder order in the viewer's score-sorted table.
    scores = [1.0 - i / max(1, len(cards_out)) for i in range(len(cards_out))]

    return {
        "checkpoint": f"composition/{ranking}",
        "commander": {"name": commander["name"], "oracle_id": commander["oracle_id"]},
        "cards": cards_out,
        "scores": scores,
        "composition": {
            "profile": profile.as_dict(),
            "breakdown": result.breakdown,
            "basics": result.basic_counts,
            "goldfish": vars(result.goldfish),
            "gate": result.gate,
            "gate_passed": result.gate_passed,
            "iterations": result.iterations,
            "warnings": result.warnings,
            "theme_density": theme_density,
        },
    }


async def build_commander_deck(
    db: AsyncSession,
    oracle_id: str,
    ranking: str = "model",
    goldfish_games: int = 400,
    save_dir: Path | None = None,
) -> dict:
    """Build a deck for a commander; returns the deck JSON (+deck_filename)."""
    result = await db.execute(
        text(
            f"SELECT {CARD_COLUMNS}, c.oracle_id::text AS oracle_id"
            f" {_FROM} WHERE c.oracle_id = CAST(:oid AS uuid) LIMIT 1"
        ),
        {"oid": oracle_id},
    )
    commander = result.mappings().first()
    if commander is None:
        raise LookupError("Commander not found (or card_facts not computed)")
    identity = frozenset(commander["color_identity"] or [])

    keys_result = await db.execute(
        text(
            "SELECT DISTINCT trigger_event FROM card_abilities"
            " WHERE card_id = CAST(:cid AS uuid) AND source = 'decompose'"
        ),
        {"cid": commander["id"]},
    )
    keys = {row[0] for row in keys_result.fetchall() if row[0]}

    pips = commander["pips"]
    if isinstance(pips, str):
        pips = json.loads(pips or "{}")
    profile = derive_profile(
        commander["name"], commander["cmc"] or 0, pips, sorted(identity), keys
    )

    pools = {
        role: [c for c in await _query_pool(db, where, identity, role) if c["id"] != commander["id"]]
        for role, where in POOL_SQL.items()
    }
    pools["theme"] = await _theme_pool(db, commander["id"], identity)
    from composition.pool_helpers import LAND_POOL_FILTER

    land_pool = await _query_pool(db, LAND_POOL_FILTER, identity, "land")
    basics = await _basics(db)
    forced = await _forced(db, identity)

    if ranking == "model":
        ranker = load_ranker()
        if ranker is None:
            raise RuntimeError("model ranking requested but checkpoints/torch unavailable")
        all_ids = list({c["id"] for pool in pools.values() for c in pool} | {commander["id"]})
        embs = await _embeddings(db, all_ids)
        cmd_emb = embs.get(commander["id"])
        if cmd_emb is None:
            raise RuntimeError(f"no embedding for commander {commander['name']}")
        loop = asyncio.get_event_loop()
        # Ramp stays heuristic: mana development is castability physics the
        # synergy model can't see (see plan W4 A/B).
        for role in pools:
            if role == "ramp":
                continue
            pools[role] = await loop.run_in_executor(
                None, partial(ranker.rank_pool, pools[role], cmd_emb, embs)
            )

    gate_relax = (
        GATE_RELAX_COST_REDUCTION
        if COST_REDUCTION_RE.search(commander["oracle_text"] or "")
        else 0.0
    )

    loop = asyncio.get_event_loop()
    build_result = await loop.run_in_executor(
        None,
        partial(
            build_deck,
            profile,
            pools,
            land_pool,
            basics,
            forced=forced,
            goldfish_games=goldfish_games,
            gate_relax=gate_relax,
        ),
    )

    theme_names = set(build_result.breakdown.get("theme", []))
    theme_ids = [c["id"] for c in build_result.deck if c["name"] in theme_names]
    density = await _theme_density(db, commander["id"], theme_ids)

    deck_json = _deck_json(profile, build_result, commander, ranking, density)

    if save_dir is not None:
        save_dir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w]+", "_", commander["name"]).strip("_")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"{stamp}_{safe}_composition.json"
        (save_dir / filename).write_text(json.dumps(deck_json, indent=2, default=str))
        deck_json["deck_filename"] = filename
        log.info("composition deck saved: %s", filename)

    return deck_json
