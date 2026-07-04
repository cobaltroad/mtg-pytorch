"""Composition evaluation harness — the plan-W6 regression check.

Builds the golden commander set, runs the hard invariants
(shared/composition/evaluation.py), and compares each deck's quota census
against the distributions human decks actually occupy (imported `decks`
table).  Human decks validate the template — they are not the target — so
range deviations are reported but only *hard* failures set the exit code.

Usage (from the ingest container; ~4-6 min for the full golden set):
    docker compose run --rm ingest python -m scripts.eval_harness
    docker compose run --rm ingest python -m scripts.eval_harness --ranking heuristic
    docker compose run --rm ingest python -m scripts.eval_harness --commanders "Wilhelt,Krenko"
    docker compose run --rm ingest python -m scripts.eval_harness --json

Exit code 0 = all hard checks pass; 1 = at least one failure.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys

import psycopg2
import psycopg2.extras

sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__file__), ".."))

from composition.evaluation import check_build, deck_census, range_check  # noqa: E402
from composition.pool_helpers import POOL_SQL  # noqa: E402
from stages.decompose import DATABASE_URL  # noqa: E402

from scripts.build_deck import build_for_commander  # noqa: E402

GOLDEN_COMMANDERS = [
    "Wilhelt, the Rotcleaver",
    "Teysa Karlov",
    "Meren of Clan Nel Toth",
    "Rhys the Redeemed",
    "Adeline, Resplendent Cathar",
    "Atraxa, Praetors' Voice",
    "Wyleth, Soul of Steel",
    "Syr Gwyn, Hero of Ashvale",
    "Mizzix of the Izmagnus",
    "Aesi, Tyrant of Gyre Strait",
    "Lathril, Blade of the Elves",
    "Krenko, Mob Boss",
    "Sythis, Harvest's Hand",
    "Muldrotha, the Gravetide",
    "Yisan, the Wanderer Bard",
    "Rograkh, Son of Rohgahh",
    "Kozilek, the Great Distortion",
    "Niv-Mizzet, Parun",
    "Hamza, Guardian of Arashin",
    "Karador, Ghost Chieftain",
]

#: Only compare census metrics a human decklist can be measured on the
#: same way (pool membership is color-blind and archetype-blind, so theme
#: has no human equivalent).
HUMAN_METRICS = ("lands", "ramp", "draw", "spot_removal", "sweepers", "protection")

MIN_HUMAN_DECK_SIZE = 95  # ignore partial imports


def human_distributions(conn) -> tuple[dict, int]:
    """Quota-census stats over imported human decks."""
    with conn.cursor() as cur:
        # Pool membership sets (color-blind — membership is per card).
        pool_ids: dict[str, set[str]] = {}
        for role, where in POOL_SQL.items():
            cur.execute(
                f"SELECT c.id::text FROM cards c JOIN card_facts f ON f.card_id = c.id"
                f" WHERE ({where}) AND (c.mana_cost IS NOT NULL OR f.is_land)"
            )
            pool_ids[role] = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT id::text FROM cards c JOIN card_facts f ON f.card_id = c.id WHERE f.is_land")
        land_ids = {r[0] for r in cur.fetchall()}

        cur.execute(
            "SELECT card_ids::text[] FROM decks WHERE array_length(card_ids, 1) >= %s",
            (MIN_HUMAN_DECK_SIZE,),
        )
        decks = [row[0] for row in cur.fetchall()]

    samples: dict[str, list[int]] = {m: [] for m in HUMAN_METRICS}
    for card_ids in decks:
        ids = list(card_ids)
        lands = sum(1 for i in ids if i in land_ids)
        if lands < 20:
            # Import artifact: a real 95+-card Commander deck never runs
            # <20 lands — these are decks whose card_ids no longer resolve.
            continue
        samples["lands"].append(lands)
        samples["ramp"].append(sum(1 for i in ids if i in pool_ids["ramp"] and i not in land_ids))
        samples["draw"].append(
            sum(1 for i in ids if (i in pool_ids["draw_engine"] or i in pool_ids["draw_spell"]) and i not in land_ids)
        )
        samples["spot_removal"].append(sum(1 for i in ids if i in pool_ids["spot_removal"] and i not in land_ids))
        samples["sweepers"].append(sum(1 for i in ids if i in pool_ids["sweeper"] and i not in land_ids))
        samples["protection"].append(sum(1 for i in ids if i in pool_ids["protection"] and i not in land_ids))

    stats = {
        metric: {
            "min": min(vals),
            "max": max(vals),
            "mean": statistics.mean(vals),
            "median": statistics.median(vals),
        }
        for metric, vals in samples.items()
        if vals
    }
    return stats, len(samples["lands"])


def color_identity_map(conn, card_ids: list[str]) -> dict[str, set[str]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id::text, color_identity FROM cards WHERE id = ANY(%s::uuid[])",
            (card_ids,),
        )
        return {cid: set(ci or []) for cid, ci in cur.fetchall()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ranking", choices=["model", "heuristic"], default="model")
    parser.add_argument("--games", type=int, default=300, help="goldfish iterations")
    parser.add_argument("--commanders", default="", help="comma-separated subset (partial names)")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    names = [n.strip() for n in args.commanders.split(",") if n.strip()] or GOLDEN_COMMANDERS

    conn = psycopg2.connect(DATABASE_URL)
    try:
        human_stats, n_human = human_distributions(conn)

        report: list[dict] = []
        n_failures = 0
        for name in names:
            try:
                profile, result = build_for_commander(
                    name, goldfish_games=args.games, ranking=args.ranking
                )
            except SystemExit as e:
                report.append({"commander": name, "failures": [f"build aborted: {e}"]})
                n_failures += 1
                continue
            ci_map = color_identity_map(conn, [c["id"] for c in result.deck])
            failures = check_build(
                profile, result, set(profile.color_identity), ci_map
            )
            census = deck_census(result)
            notes = range_check(census, human_stats)
            n_failures += len(failures)
            report.append({
                "commander": profile.commander_name,
                "census": census,
                "goldfish_p": round(result.goldfish.p_commander_by_go_live, 3),
                "gate": result.gate,
                "theme_density": getattr(result, "theme_density", None),
                "failures": failures,
                "human_range_notes": notes,
                "warnings": result.warnings,
            })
    finally:
        conn.close()

    if args.as_json:
        print(json.dumps({
            "ranking": args.ranking,
            "human_decks": n_human,
            "human_stats": human_stats,
            "results": report,
            "hard_failures": n_failures,
        }, indent=2, default=str))
    else:
        print(f"\nHuman quota distributions ({n_human} decks ≥{MIN_HUMAN_DECK_SIZE} cards):")
        for metric, s in human_stats.items():
            print(f"  {metric:<13} min {s['min']:>3}  median {s['median']:>5.1f}  "
                  f"mean {s['mean']:>5.1f}  max {s['max']:>3}")
        print(f"\nGolden set ({args.ranking} ranking):")
        for r in report:
            status = "FAIL" if r["failures"] else "OK  "
            census = r.get("census", {})
            print(f"{status} {r['commander'][:32]:<33} "
                  + " ".join(f"{k[:4]}={v}" for k, v in census.items())
                  + f"  P={r.get('goldfish_p', '?')}")
            for f in r["failures"]:
                print(f"       ✗ {f}")
            for n in r.get("human_range_notes", []):
                print(f"       ~ {n}")
        print(f"\n{'PASS' if n_failures == 0 else 'FAIL'}: "
              f"{n_failures} hard failure(s) across {len(report)} commanders")

    sys.exit(1 if n_failures else 0)


if __name__ == "__main__":
    main()
