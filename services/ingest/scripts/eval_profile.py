"""Spot-check derived composition profiles for real commanders (plan W2).

Looks a commander up in the DB (partial name match, same as
eval_decomposition), runs the decompose patterns live, and prints the
CompositionProfile the quota engine derives for it.

Usage:
    docker compose run --rm ingest python -m scripts.eval_profile "Wilhelt"
    docker compose run --rm ingest python -m scripts.eval_profile "Syr Gwyn"
"""
from __future__ import annotations

import json
import os
import sys

import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from composition.profile import derive_profile  # noqa: E402
from stages.decompose import DATABASE_URL, _detect, _fetch  # noqa: E402


def _pips(card_id: str) -> dict[str, int]:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pips FROM card_facts WHERE card_id = %s::uuid", (card_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return {}
    return row[0] if isinstance(row[0], dict) else json.loads(row[0])


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    name = sys.argv[1]

    cards = _fetch(name)
    if not cards:
        sys.exit(f"No commander matching {name!r}")
    card = cards[0]

    keys = {key for key, _label, _phrase in _detect(card["oracle_text"] or "", card["type_line"] or "")}
    profile = derive_profile(
        commander_name=card["name"],
        mana_value=card["cmc"] or 0,
        pips=_pips(card["id"]),
        color_identity=card["color_identity"] or [],
        decompose_keys=keys,
    )

    print(f"\n{card['name']}  (MV {profile.commander_mv}, {''.join(profile.color_identity) or 'C'})")
    print(f"signals: {', '.join(sorted(keys)) or '(none)'}")
    print(f"\ngo live: turn {profile.go_live_turn} — {profile.go_live_because}\n")
    rows = [
        ("lands", profile.lands),
        ("ramp", profile.ramp),
        ("draw", profile.draw),
        ("spot removal", profile.spot_removal),
        ("sweepers", profile.sweepers),
        ("protection", profile.protection),
        ("theme", profile.theme),
    ]
    for label, quota in rows:
        extra = f"  (≤{quota.max_mv} MV)" if label == "ramp" else ""
        extra = f"  ({quota.engines} engines / {quota.spells} spells)" if label == "draw" else extra
        print(f"  {label:<13} {quota.count:>3}{extra}  — {quota.because}")
    print(f"  {'total':<13} {profile.slot_total():>3}")

    print("\ncurve targets (spells):")
    print("  " + "  ".join(
        f"≤{t.max_mv}: {t.count}" if t.max_mv < 99 else f"6+: {t.count}"
        for t in profile.curve_targets
    ))

    if profile.pip_requirements:
        print("\ncommander castability (sources needed in the 99):")
        for r in profile.pip_requirements:
            print(f"  {r.pips}×{{{r.color}}} by turn {r.by_turn} → {r.sources} {r.color} sources")
    print()


if __name__ == "__main__":
    main()
