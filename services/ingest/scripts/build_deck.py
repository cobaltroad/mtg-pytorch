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
import re
import sys

import psycopg2
import psycopg2.extras

sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__file__), ".."))

from composition.builder import build_deck  # noqa: E402
from composition.profile import CompositionProfile, derive_profile  # noqa: E402
from mtg_sql.staples import (  # noqa: E402
    draw_engine,
    draw_spell,
    interaction,
    protection,
    removal,
    sweeper,
)
from mtg_sql.staples.ramp import SQL as RAMP_SQL  # noqa: E402
from stages.decompose import DATABASE_URL, _detect, _fetch  # noqa: E402
from synergy.commander_mechanics import PATTERN_KEY_TO_CONSUMER_SQL  # noqa: E402

FORCED_NAMES = ("Sol Ring", "Arcane Signet", "Command Tower")

_POOL_SQL: dict[str, str] = {
    "ramp": RAMP_SQL,
    "draw_engine": draw_engine.SQL,
    "draw_spell": draw_spell.SQL,
    "spot_removal": f"(({removal.SQL}) OR ({interaction.SQL}))",
    "sweeper": sweeper.SQL,
    "protection": protection.SQL,
}

_CARD_COLUMNS = (
    "c.id::text, c.name, c.cmc, c.color_identity, c.produced_mana, c.type_line,"
    " c.oracle_text, f.pips, f.hybrid_pips, f.is_land, f.is_basic, f.etb_tapped, f.is_fetch"
)

# Largest "Add {…}{…}" clause → mana per activation (Sol Ring 2, Thran
# Dynamo 3).  The goldfisher counts each source once per turn, so this is
# what makes big-mana commanders castable in simulation.
_ADD_CLAUSE_RE = re.compile(r"add ((?:\{[WUBRGCS0-9]\})+)", re.IGNORECASE)
_ADD_SYMBOL_RE = re.compile(r"\{[WUBRGCS0-9]\}")


def _mana_output(oracle_text: str | None) -> int:
    best = 1
    for clause in _ADD_CLAUSE_RE.findall(oracle_text or ""):
        best = max(best, len(_ADD_SYMBOL_RE.findall(clause)))
    return best


def _row_to_card(row, roles: set[str]) -> dict:
    pips = row["pips"] if isinstance(row["pips"], dict) else json.loads(row["pips"] or "{}")
    hybrid = row["hybrid_pips"]
    if not isinstance(hybrid, list):
        hybrid = json.loads(hybrid or "[]")
    return {
        "id": row["id"],
        "name": row["name"],
        "mv": int(row["cmc"] or 0),
        "pips": pips,
        "hybrid": hybrid,
        "is_land": row["is_land"],
        "is_basic": row["is_basic"],
        "produces": [c for c in (row["produced_mana"] or []) if c in "WUBRGC"],
        "mana_output": _mana_output(row["oracle_text"]),
        "etb_tapped": row["etb_tapped"],
        "is_fetch": row["is_fetch"],
        "roles": set(roles),
    }


def _query_pool(conn, where_sql: str, identity: frozenset[str], role: str) -> list[dict]:
    query = (
        f"SELECT {_CARD_COLUMNS} FROM cards c JOIN card_facts f ON f.card_id = c.id"
        f" WHERE ({where_sql})"
        # No-cost nonland cards (Evermind, suspend-only spells) are
        # uncastable by normal means — keep them out of heuristic pools.
        " AND (c.mana_cost IS NOT NULL OR f.is_land)"
    )
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(query)
        rows = cur.fetchall()
    cards = [
        _row_to_card(r, {role})
        for r in rows
        if frozenset(r["color_identity"] or []) <= identity
    ]
    # Heuristic rank (the W3 baseline the ML must beat): ramp prefers raw
    # mana output (Thran Dynamo over trinkets), then the 2-MV signet tier;
    # everything else cheap-first; name breaks ties.
    if role == "ramp":
        cards.sort(key=lambda c: (-min(c["mana_output"], 3), abs(c["mv"] - 2), c["name"]))
    else:
        cards.sort(key=lambda c: (c["mv"], c["name"]))
    return cards


def _theme_pool(conn, keys: set[str], identity: frozenset[str], commander_id: str) -> list[dict]:
    """Union of decompose consumer pools; rank by how many keys a card serves."""
    hits: dict[str, dict] = {}
    matches: dict[str, int] = {}
    for key in sorted(keys):
        where = PATTERN_KEY_TO_CONSUMER_SQL.get(key)
        if not where:
            continue
        for card in _query_pool(conn, where, identity, "theme"):
            if card["id"] == commander_id:
                continue
            hits.setdefault(card["id"], card)
            matches[card["id"]] = matches.get(card["id"], 0) + 1
    pool = list(hits.values())
    pool.sort(key=lambda c: (-matches[c["id"]], c["mv"], c["name"]))
    return pool


def _land_pool(conn, identity: frozenset[str]) -> list[dict]:
    where = "f.is_land AND NOT f.is_basic"
    return _query_pool(conn, where, identity, "land")


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


def build_for_commander(name: str, goldfish_games: int = 500) -> tuple[CompositionProfile, object]:
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

        # The goldfisher can't model a commander discounting its own cost
        # (Karador); relax its gate rather than pretend the sim is right.
        gate_relax = 0.0
        if re.search(r"costs? \{?[X0-9]*\}? ?less to cast", commander["oracle_text"] or "", re.I):
            gate_relax = 0.15

        result = build_deck(
            profile,
            pools,
            _land_pool(conn, identity),
            _basics(conn),
            forced=_forced(conn, identity),
            goldfish_games=goldfish_games,
            gate_relax=gate_relax,
        )
    finally:
        conn.close()
    return profile, result


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    profile, result = build_for_commander(args[0])

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
    print(f"\n=== {profile.commander_name} — composition build (heuristic baseline) ===")
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
