"""Slot-dispute report — the pool-SQL review queue (#182).

'slot'-kind votes in card_votes mean pool membership is wrong: a staple-SQL
or decompose-pattern bug, never a model label (design §2, decision 2).
This report lists each disputed (card, slot) pair together with the *named
SQL mode* that currently matches the card (removal.DESTROY,
protection.GRANT, …) and the file to fix, so each dispute resolves as a
rule-fix PR like the #136 tranches.

A dispute is OPEN while the card still matches the disputed slot's pool
SQL (or, for theme, still has a decomposed_candidates edge from the
commander).  Once the rule is fixed and the pool no longer claims the
card, the dispute drops off automatically — vote rows are kept as history.

Exit code: 1 while open disputes remain, 0 when the queue is empty
(harness convention — lets "queue empty" be checked in scripts).

Usage:
    docker compose run --rm ingest python -m scripts.report_slot_disputes
    docker compose run --rm ingest python -m scripts.report_slot_disputes --json
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid

import psycopg2
import psycopg2.extras

from stages.decompose import DATABASE_URL  # noqa: E402
from composition.pool_helpers import FORCED_NAMES, POOL_SQL
from mtg_sql.staples import (
    draw_engine,
    draw_spell,
    interaction,
    protection,
    removal,
    sweeper,
    wincon,
)
from mtg_sql.staples.ramp import SQL as RAMP_SQL

#: Slot → named WHERE fragments.  Names mirror the module attributes so a
#: report line reads as a pointer into shared/mtg_sql/staples.  Kept in
#: sync with POOL_SQL by tests/test_slot_disputes.py (substring check).
SLOT_MODES: dict[str, dict[str, str]] = {
    "ramp": {"ramp.SQL": RAMP_SQL},
    "draw_engine": {"draw_engine.SQL": draw_engine.SQL},
    "draw_spell": {"draw_spell.SQL": draw_spell.SQL},
    "spot_removal": {
        "removal.DESTROY": removal.DESTROY,
        "removal.DAMAGE": removal.DAMAGE,
        "removal.EXILE": removal.EXILE,
        "removal.BOUNCE": removal.BOUNCE,
        "interaction.COUNTERSPELLS": interaction.COUNTERSPELLS,
    },
    "sweeper": {"sweeper.SQL": sweeper.SQL},
    "protection": {
        "protection.GRANT": protection.GRANT,
        "protection.PHASE": protection.PHASE,
        "protection.FLICKER": protection.FLICKER,
    },
    "wincon": {
        "wincon.ALT_WIN": wincon.ALT_WIN,
        "wincon.X_SCALER": wincon.X_SCALER,
        "wincon.OVERRUN": wincon.OVERRUN,
        "wincon.EXTRA_COMBAT": wincon.EXTRA_COMBAT,
    },
}

_STAPLE_DIR = "shared/mtg_sql/staples"
FIX_LOCATION: dict[str, str] = {
    "ramp.SQL": f"{_STAPLE_DIR}/ramp.py",
    "draw_engine.SQL": f"{_STAPLE_DIR}/draw_engine.py",
    "draw_spell.SQL": f"{_STAPLE_DIR}/draw_spell.py",
    "removal.DESTROY": f"{_STAPLE_DIR}/removal.py",
    "removal.DAMAGE": f"{_STAPLE_DIR}/removal.py",
    "removal.EXILE": f"{_STAPLE_DIR}/removal.py",
    "removal.BOUNCE": f"{_STAPLE_DIR}/removal.py",
    "interaction.COUNTERSPELLS": f"{_STAPLE_DIR}/interaction.py",
    "sweeper.SQL": f"{_STAPLE_DIR}/sweeper.py",
    "protection.GRANT": f"{_STAPLE_DIR}/protection.py",
    "protection.PHASE": f"{_STAPLE_DIR}/protection.py",
    "protection.FLICKER": f"{_STAPLE_DIR}/protection.py",
    "wincon.ALT_WIN": f"{_STAPLE_DIR}/wincon.py",
    "wincon.X_SCALER": f"{_STAPLE_DIR}/wincon.py",
    "wincon.OVERRUN": f"{_STAPLE_DIR}/wincon.py",
    "wincon.EXTRA_COMBAT": f"{_STAPLE_DIR}/wincon.py",
    "theme": ("services/ingest/stages/decompose.py ORACLE_PATTERNS + "
              "services/ingest/synergy/commander_mechanics.py (then re-run "
              "decompose_commanders + compute_commander_value_synergy)"),
    "forced": "shared/composition/pool_helpers.py FORCED_NAMES",
}


def _fetch_disputes(conn) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT v.slot, v.vote, v.deck_ref, v.created_at::date AS voted,
                   cmd.id::text AS commander_id, cmd.name AS commander,
                   c.id::text   AS card_id,      c.name   AS card
            FROM card_votes v
            JOIN cards cmd ON cmd.id = v.commander_id
            JOIN cards c   ON c.id = v.card_id
            WHERE v.kind = 'slot'
            ORDER BY v.created_at
        """)
        return [dict(r) for r in cur.fetchall()]


def _matching_modes(conn, card_id: str, slot: str) -> list[str]:
    """Named staple fragments that still claim this card for this slot."""
    cid = uuid.UUID(card_id)  # validate before literal embedding
    matched = []
    with conn.cursor() as cur:
        for mode, fragment in SLOT_MODES[slot].items():
            # No-param execute, same as the build path: staple fragments
            # carry '%%' escapes and '(?:…)' regex colons.
            cur.execute(
                f"SELECT 1 FROM cards c JOIN card_facts f ON f.card_id = c.id"
                f" WHERE c.id = '{cid}' AND ({fragment})"
            )
            if cur.fetchone():
                matched.append(mode)
    return matched


def _theme_keys(conn, commander_id: str, card_id: str) -> list[str] | None:
    """pattern_keys on the decomposed_candidates edge, or None if no edge."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT metadata FROM synergy_edges"
            " WHERE card_a = %s AND card_b = %s"
            "   AND score_type = 'decomposed_candidates'",
            (commander_id, card_id),
        )
        row = cur.fetchone()
    if row is None:
        return None
    meta = row[0]
    if isinstance(meta, str):
        meta = json.loads(meta or "{}")
    return sorted((meta or {}).get("pattern_keys") or [])


def classify(conn, d: dict) -> dict:
    """Resolve one dispute row against the live pool rules."""
    slot = d["slot"] or ""
    out = {**d, "modes": [], "fix": None, "status": "open"}
    if slot == "theme":
        keys = _theme_keys(conn, d["commander_id"], d["card_id"])
        if keys is None:
            out["status"] = "resolved"
        else:
            out["modes"] = [f"pattern:{k}" for k in keys] or ["edge (no pattern_keys)"]
            out["fix"] = FIX_LOCATION["theme"]
    elif slot == "forced":
        if d["card"] in FORCED_NAMES:
            out["modes"] = ["FORCED_NAMES"]
            out["fix"] = FIX_LOCATION["forced"]
        else:
            out["status"] = "resolved"
    elif slot in SLOT_MODES:
        modes = _matching_modes(conn, d["card_id"], slot)
        if modes:
            out["modes"] = modes
            out["fix"] = "; ".join(dict.fromkeys(FIX_LOCATION[m] for m in modes))
        else:
            out["status"] = "resolved"
    else:
        # Land slots etc. — not pool-SQL governed; needs a human look.
        out["modes"] = [f"(no pool SQL for slot {slot!r})"]
        out["fix"] = "manual review"
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    conn = psycopg2.connect(DATABASE_URL)
    try:
        results = [classify(conn, d) for d in _fetch_disputes(conn)]
    finally:
        conn.close()

    open_d = [r for r in results if r["status"] == "open"]
    resolved = [r for r in results if r["status"] == "resolved"]

    if args.json:
        print(json.dumps({
            "open": [{k: str(v) if k == "voted" else v for k, v in r.items()}
                     for r in open_d],
            "resolved": len(resolved),
        }, indent=2))
    else:
        if not results:
            print("No slot disputes on record.")
        for r in open_d:
            print(f"OPEN  {r['card']:<32} slot={r['slot']:<13} "
                  f"({r['commander']}, {r['voted']})")
            print(f"      matched: {', '.join(r['modes'])}")
            print(f"      fix:     {r['fix']}")
            print(f"      deck:    {r['deck_ref']}")
        if resolved:
            names = ", ".join(f"{r['card']} [{r['slot']}]" for r in resolved)
            print(f"\nresolved (dropped off): {len(resolved)} — {names}")
        print(f"\n{len(open_d)} open dispute(s), {len(resolved)} resolved")

    sys.exit(1 if open_d else 0)


if __name__ == "__main__":
    main()
