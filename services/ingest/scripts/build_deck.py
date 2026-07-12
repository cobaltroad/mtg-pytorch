"""Build a deck with the composition engine — heuristic W3 baseline, live DB.

The DB glue for shared/composition/builder.py: derives the commander's
profile, assembles color-legal candidate pools from the staple SQL modules
plus decompose consumer SQL (theme), ranks them heuristically (no ML), and
prints the built deck with quota breakdown and goldfish metrics.

Usage:
    docker compose run --rm ingest python -m scripts.build_deck "Wilhelt"
    docker compose run --rm ingest python -m scripts.build_deck "Syr Gwyn" --json
"""
from __future__ import annotations

import json
import sys

import psycopg2
import psycopg2.extras

sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__file__), ".."))

from composition.builder import build_deck  # noqa: E402
from composition.pool_helpers import (  # noqa: E402
    CARD_COLUMNS as _CARD_COLUMNS,
    CASTABLE_FILTER,
    COST_REDUCTION_RE,
    FORCED_NAMES,
    POOL_SQL as _POOL_SQL,
    row_to_card as _row_to_card,
    sort_pool,
)
from composition.profile import CompositionProfile, derive_profile  # noqa: E402
from stages.decompose import DATABASE_URL, _detect, _fetch  # noqa: E402
from synergy.commander_mechanics import PATTERN_KEY_TO_CONSUMER_SQL  # noqa: E402


def _query_pool(conn, where_sql: str, identity: frozenset[str], role: str) -> list[dict]:
    query = (
        f"SELECT {_CARD_COLUMNS} FROM cards c JOIN card_facts f ON f.card_id = c.id"
        f" WHERE ({where_sql}) AND {CASTABLE_FILTER}"
    )
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(query)
        rows = cur.fetchall()
    cards = [
        _row_to_card(r, {role})
        for r in rows
        if frozenset(r["color_identity"] or []) <= identity
    ]
    return sort_pool(cards, role)


def _theme_pool(conn, keys: set[str], identity: frozenset[str], commander_id: str) -> list[dict]:
    """Union of decompose consumer pools; rank by how many keys a card serves.

    Each card carries `theme_keys` (which sub-themes it serves) so the
    builder's diminishing-returns counters can saturate sub-themes.
    """
    hits: dict[str, dict] = {}
    for key in sorted(keys):
        where = PATTERN_KEY_TO_CONSUMER_SQL.get(key)
        if not where:
            continue
        for card in _query_pool(conn, where, identity, "theme"):
            if card["id"] == commander_id:
                continue
            entry = hits.setdefault(card["id"], card)
            entry.setdefault("theme_keys", set()).add(key)
    pool = list(hits.values())
    pool.sort(key=lambda c: (-len(c["theme_keys"]), c["edhrec_rank"], c["name"]))
    return pool


def _embeddings(conn, card_ids: list[str]) -> dict[str, list[float]]:
    """Raw 768-dim embeddings for the given cards (missing ids omitted)."""
    if not card_ids:
        return {}
    out: dict[str, list[float]] = {}
    with conn.cursor() as cur:
        for i in range(0, len(card_ids), 5000):
            batch = card_ids[i : i + 5000]
            cur.execute(
                "SELECT card_id::text, embedding::text FROM card_embeddings"
                " WHERE card_id = ANY(%s::uuid[])",
                (batch,),
            )
            for cid, vec in cur.fetchall():
                out[cid] = json.loads(vec)
    return out


def _theme_density(conn, commander_id: str, theme_ids: list[str]) -> dict:
    """Synergy-edge density of the chosen theme slots (A/B metric).

    commander_edge_rate — fraction of theme cards with a synergy edge to
    the commander; pairwise_rate — density of edges among theme cards.
    """
    if not theme_ids:
        return {"commander_edge_rate": 0.0, "pairwise_rate": 0.0}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(DISTINCT CASE WHEN card_a = %s::uuid THEN card_b ELSE card_a END)"
            " FROM synergy_edges"
            " WHERE (card_a = %s::uuid AND card_b = ANY(%s::uuid[]))"
            "    OR (card_b = %s::uuid AND card_a = ANY(%s::uuid[]))",
            (commander_id, commander_id, theme_ids, commander_id, theme_ids),
        )
        cmd_hits = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*) FROM synergy_edges"
            " WHERE card_a = ANY(%s::uuid[]) AND card_b = ANY(%s::uuid[])",
            (theme_ids, theme_ids),
        )
        pair_hits = cur.fetchone()[0]
    n = len(theme_ids)
    return {
        "commander_edge_rate": cmd_hits / n,
        "pairwise_rate": pair_hits / (n * (n - 1)) if n > 1 else 0.0,
    }


def _land_pool(conn, identity: frozenset[str]) -> list[dict]:
    from composition.pool_helpers import LAND_POOL_FILTER

    return _query_pool(conn, LAND_POOL_FILTER, identity, "land")


def _basics(conn) -> dict[str, dict]:
    subtype_to_color = {"Plains": "W", "Island": "U", "Swamp": "B",
                        "Mountain": "R", "Forest": "G"}
    out: dict[str, dict] = {}
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        for subtype, color in subtype_to_color.items():
            cur.execute(
                f"SELECT {_CARD_COLUMNS} FROM cards c JOIN card_facts f ON f.card_id = c.id"
                " WHERE c.type_line ILIKE %s LIMIT 1",
                (f"Basic Land — {subtype}",),
            )
            row = cur.fetchone()
            if row:
                out[color] = _row_to_card(row, set())
        # Wastes has no land subtype — its type line is just "Basic Land".
        cur.execute(
            f"SELECT {_CARD_COLUMNS} FROM cards c JOIN card_facts f ON f.card_id = c.id"
            " WHERE c.name = 'Wastes' LIMIT 1"
        )
        row = cur.fetchone()
        if row:
            out["C"] = _row_to_card(row, set())
    return out


def _forced(conn, identity: frozenset[str]) -> list[dict]:
    out = []
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        for name in FORCED_NAMES:
            cur.execute(
                f"SELECT {_CARD_COLUMNS} FROM cards c JOIN card_facts f ON f.card_id = c.id"
                " WHERE c.name = %s LIMIT 1",
                (name,),
            )
            row = cur.fetchone()
            if row and frozenset(row["color_identity"] or []) <= identity:
                roles = {"ramp"} if not row["is_land"] else set()
                out.append(_row_to_card(row, roles))
    return out


def build_for_commander(
    name: str,
    goldfish_games: int = 500,
    ranking: str = "heuristic",
) -> tuple[CompositionProfile, object]:
    """ranking: 'heuristic' (W3 baseline) or 'model' (Phase 1/2 re-ranked)."""
    cards = _fetch(name)
    if not cards:
        sys.exit(f"No commander matching {name!r}")
    commander = cards[0]
    identity = frozenset(commander["color_identity"] or [])

    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("SELECT pips FROM card_facts WHERE card_id = %s::uuid", (commander["id"],))
            row = cur.fetchone()
        pips = {}
        if row:
            pips = row[0] if isinstance(row[0], dict) else json.loads(row[0])

        keys = {k for k, _l, _p in _detect(commander["oracle_text"] or "", commander["type_line"] or "")}
        profile = derive_profile(commander["name"], commander["cmc"] or 0, pips,
                                 sorted(identity), keys)

        pools = {
            role: [c for c in _query_pool(conn, where, identity, role) if c["id"] != commander["id"]]
            for role, where in _POOL_SQL.items()
        }
        pools["theme"] = _theme_pool(conn, keys, identity, commander["id"])

        if ranking == "model":
            from composition.ranking import load_ranker

            ranker = load_ranker()
            if ranker is None:
                sys.exit("model ranking requested but checkpoints/torch unavailable")
            all_ids = [c["id"] for pool in pools.values() for c in pool] + [commander["id"]]
            embs = _embeddings(conn, list(set(all_ids)))
            cmd_emb = embs.get(commander["id"])
            if cmd_emb is None:
                sys.exit(f"no embedding for commander {commander['name']}")
            # Ramp stays heuristic: mana development is castability physics
            # (mana output per card), which the synergy model can't see —
            # model-ranking it demotes the big rocks Kozilek-tier decks
            # need and fails the castability gate.
            pools = {
                role: pool if role == "ramp" else ranker.rank_pool(pool, cmd_emb, embs)
                for role, pool in pools.items()
            }

        # Commanders that discount their own cost (Karador) get a per-turn
        # generic discount simulated in the goldfisher (#142).
        cost_reduction = bool(COST_REDUCTION_RE.search(commander["oracle_text"] or ""))

        result = build_deck(
            profile,
            pools,
            _land_pool(conn, identity),
            _basics(conn),
            forced=_forced(conn, identity),
            goldfish_games=goldfish_games,
            cost_reduction=cost_reduction,
        )

        theme_names = set(result.breakdown.get("theme", []))
        theme_ids = [c["id"] for c in result.deck if c["name"] in theme_names]
        result.theme_density = _theme_density(conn, commander["id"], theme_ids)
    finally:
        conn.close()
    return profile, result


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    ranking = "model" if "--ranking=model" in sys.argv or "--model" in sys.argv else "heuristic"
    profile, result = build_for_commander(args[0], ranking=ranking)

    if "--json" in sys.argv:
        print(json.dumps({
            "profile": profile.as_dict(),
            "deck": [c["name"] for c in result.deck],
            "breakdown": result.breakdown,
            "basics": result.basic_counts,
            "goldfish": vars(result.goldfish),
            "gate": result.gate,
            "gate_passed": result.gate_passed,
            "warnings": result.warnings,
        }, indent=2))
        return

    g = result.goldfish
    check_turn = max(profile.go_live_turn, profile.commander_mv)
    print(f"\n=== {profile.commander_name} — composition build ({ranking} ranking) ===")
    d = result.theme_density
    print(f"theme density: commander_edge_rate={d['commander_edge_rate']:.2f} "
          f"pairwise_rate={d['pairwise_rate']:.3f}")
    lands = sum(1 for c in result.deck if c["is_land"])
    print(f"99 cards: {lands} lands ({result.basic_counts}), {99 - lands} spells")
    print(f"goldfish: P(cast by T{check_turn}) = {g.p_commander_by_go_live:.2f} "
          f"(gate {result.gate:.2f} {'PASS' if result.gate_passed else 'FAIL'}), "
          f"avg cast T{g.avg_cast_turn:.1f}, keepable {g.keepable_rate:.2f}")
    for w in result.warnings:
        print(f"  ⚠ {w}")
    print()
    for slot, names in result.breakdown.items():
        if not names:
            continue
        print(f"{slot} ({len(names)}):")
        for n in names:
            print(f"  {n}")
    print()


if __name__ == "__main__":
    main()
