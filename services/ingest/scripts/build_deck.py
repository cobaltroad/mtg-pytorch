"""Build a deck with the composition engine — heuristic W3 baseline, live DB.

The DB glue for shared/composition/builder.py: derives the commander's
profile, assembles color-legal candidate pools from the staple SQL modules
plus decompose consumer SQL (theme), ranks them heuristically (no ML), and
prints the built deck with quota breakdown and goldfish metrics.

Usage:
    docker compose run --rm ingest python -m scripts.build_deck "Wilhelt"
    docker compose run --rm ingest python -m scripts.build_deck "Syr Gwyn" --json
    docker compose run --rm ingest python -m scripts.build_deck "Rograkh" --partner "Silas Renn"
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
    apply_vote_overrides,
    row_to_card as _row_to_card,
    sort_pool,
)
from composition.profile import (  # noqa: E402
    CompositionProfile,
    derive_partner_profile,
    derive_profile,
)
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


def _commander_inputs(conn, name: str) -> dict:
    """Fetch one commander's profile inputs (partial name match)."""
    cards = _fetch(name)
    if not cards:
        sys.exit(f"No commander matching {name!r}")
    c = cards[0]
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("SELECT pips FROM card_facts WHERE card_id = %s::uuid", (c["id"],))
        row = cur.fetchone()
    pips = {}
    if row:
        pips = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    keys = {k for k, _l, _p in _detect(c["oracle_text"] or "", c["type_line"] or "")}
    return {
        "id": c["id"],
        "name": c["name"],
        "oracle_text": c["oracle_text"],
        "mana_value": c["cmc"] or 0,
        "pips": pips,
        "color_identity": sorted(c["color_identity"] or []),
        "decompose_keys": keys,
    }


def build_for_commander(
    name: str,
    goldfish_games: int = 500,
    ranking: str = "heuristic",
    partner: str | None = None,
    honor_votes: bool = False,
) -> tuple[CompositionProfile, object]:
    """ranking: 'heuristic' (W3 baseline) or 'model' (Phase 1/2 re-ranked).
    partner: second commander name for partner pairs (#147) — 98-card deck.
    honor_votes: amend pass (#184) — pin net-upvoted, exclude net-downvoted.
    """
    conn = psycopg2.connect(DATABASE_URL)
    try:
        commanders = [_commander_inputs(conn, name)]
        if partner:
            commanders.append(_commander_inputs(conn, partner))
        commander = commanders[0]
        identity = frozenset(c for cmd in commanders for c in cmd["color_identity"])
        keys = {k for cmd in commanders for k in cmd["decompose_keys"]}

        if partner:
            profile = derive_partner_profile(commanders)
        else:
            profile = derive_profile(commander["name"], commander["mana_value"],
                                     commander["pips"], sorted(identity), keys)

        commander_ids = {cmd["id"] for cmd in commanders}
        pools = {
            role: [c for c in _query_pool(conn, where, identity, role) if c["id"] not in commander_ids]
            for role, where in _POOL_SQL.items()
        }
        theme_seen: dict = {}
        for cmd in commanders:
            for card in _theme_pool(conn, cmd["decompose_keys"], identity, cmd["id"]):
                if card["id"] in commander_ids:
                    continue
                if card["id"] in theme_seen:
                    theme_seen[card["id"]]["theme_keys"] |= card.get("theme_keys", set())
                else:
                    theme_seen[card["id"]] = card
        pools["theme"] = sorted(
            theme_seen.values(),
            key=lambda c: (-len(c.get("theme_keys", ())), c["edhrec_rank"], c["name"]),
        )

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

        land_pool = _land_pool(conn, identity)
        forced = _forced(conn, identity)
        if honor_votes:
            # After ranking, so pins survive the model re-sort (#184).
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT card_id::text, SUM(vote) FROM card_votes"
                    " WHERE kind = 'fit' AND commander_id = ANY(%s::uuid[])"
                    " GROUP BY card_id",
                    (list(commander_ids),),
                )
                nets = {cid: int(net) for cid, net in cur.fetchall()}
            pools, land_pool, forced, pinned, unplaced = apply_vote_overrides(
                pools, land_pool, forced,
                {c for c, n in nets.items() if n > 0},
                {c for c, n in nets.items() if n < 0},
            )
            print(f"vote overrides: {len(pinned)} pinned, "
                  f"{sum(1 for n in nets.values() if n < 0)} excluded, "
                  f"{len(unplaced)} pins not in any pool")

        # Commanders that discount their own cost (Karador) get a per-turn
        # generic discount simulated in the goldfisher (#142).
        cost_reduction = any(
            COST_REDUCTION_RE.search(cmd["oracle_text"] or "") for cmd in commanders
        )

        result = build_deck(
            profile,
            pools,
            land_pool,
            _basics(conn),
            forced=forced,
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
    honor_votes = "--honor-votes" in sys.argv
    partner = None
    for i, a in enumerate(sys.argv):
        if a == "--partner" and i + 1 < len(sys.argv):
            partner = sys.argv[i + 1]
        elif a.startswith("--partner="):
            partner = a.split("=", 1)[1]
    if partner in args:
        args.remove(partner)
    profile, result = build_for_commander(args[0], ranking=ranking, partner=partner,
                                          honor_votes=honor_votes)

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
    total = len(result.deck)
    print(f"{total} cards: {lands} lands ({result.basic_counts}), {total - lands} spells")
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
