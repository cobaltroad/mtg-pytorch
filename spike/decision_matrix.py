"""
Spike #46 — Step 4: Render the weighted decision matrix.

Reads bench results from spike/results/ and fills in the decision matrix
from the spike issue.  Run this any time to see the current state; it
handles partial results gracefully (missing cells shown as '—').

Weights
-------
  Synergy separation (cosine)      30 %
  Throughput (cards or pairs/sec)  20 %
  Estimated re-train wall time     20 %  (manual input — see --retrain-hours)
  Qualitative top-10 neighbours    20 %  (manual input — see --qualitative)
  Operational complexity           10 %  (manual input — see --complexity)

Usage
-----
    # Just show what the benchmarks have produced so far:
    python spike/decision_matrix.py

    # Fill in the manually-assessed criteria:
    python spike/decision_matrix.py \
        --retrain-hours MiniLM=48 all-mpnet=52 gte-large=60 e5-large=60 \
        --qualitative   MiniLM=3  all-mpnet=3  gte-large=4  e5-large=4  \
        --complexity    MiniLM=1  all-mpnet=1  gte-large=1  e5-large=2  \
        --llm-retrain-hours mistral-7b=120 \
        --llm-qualitative   mistral-7b=3 \
        --llm-complexity    mistral-7b=4

    # Save a snapshot:
    python spike/decision_matrix.py --save
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"

# ── Weights ───────────────────────────────────────────────────────────────────

CRITERIA: list[tuple[str, float, str]] = [
    # (key, weight, label)
    ("separation",      0.30, "Synergy separation (cosine)"),
    ("throughput",      0.20, "Throughput (cards or pairs/sec)"),
    ("retrain_hours",   0.20, "Re-train wall time (lower=better)"),
    ("qualitative",     0.20, "Qualitative top-10 (1–5 scale)"),
    ("complexity",      0.10, "Operational complexity (1=simple)"),
]


def _parse_kv(items: list[str] | None) -> dict[str, float]:
    """Parse 'key=value' strings into a dict."""
    if not items:
        return {}
    out: dict[str, float] = {}
    for item in items:
        k, _, v = item.partition("=")
        try:
            out[k.strip()] = float(v.strip())
        except ValueError:
            pass
    return out


def load_path_a() -> list[dict]:
    p = RESULTS_DIR / "path_a.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return []


def load_path_b() -> list[dict]:
    p = RESULTS_DIR / "path_b.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return []


def normalise_column(values: dict[str, float | None], higher_is_better: bool) -> dict[str, float]:
    """
    Min-max normalise a dict of {model: raw_value} to [0, 1].
    None values are excluded from the range and mapped to None.
    """
    present = {k: v for k, v in values.items() if v is not None}
    if not present:
        return {k: None for k in values}

    lo, hi = min(present.values()), max(present.values())
    result: dict[str, float | None] = {}
    for k, v in values.items():
        if v is None:
            result[k] = None
        elif hi == lo:
            result[k] = 1.0
        else:
            norm = (v - lo) / (hi - lo)
            result[k] = norm if higher_is_better else 1.0 - norm
    return result


def build_matrix(
    path_a: list[dict],
    path_b: list[dict],
    retrain_hours: dict[str, float],
    qualitative: dict[str, float],
    complexity: dict[str, float],
    llm_retrain_hours: dict[str, float],
    llm_qualitative: dict[str, float],
    llm_complexity: dict[str, float],
) -> tuple[list[str], dict[str, dict[str, float | None]]]:
    """
    Returns (model_names, {model: {criterion_key: raw_value | None}}).
    """
    # ── Collect Path A rows ───────────────────────────────────────────────────
    rows: dict[str, dict] = {}
    for r in path_a:
        name = r["model"]
        rows[name] = {
            "separation":    r.get("separation"),
            "throughput":    r.get("throughput_cards_per_sec"),
            "retrain_hours": retrain_hours.get(name),
            "qualitative":   qualitative.get(name),
            "complexity":    complexity.get(name),
            "path":          "A",
        }

    # ── Collect Path B rows ───────────────────────────────────────────────────
    # Prefer the 'embedding' strategy for separation comparison; fall back to logit
    llm_sep: dict[str, float | None] = {}
    llm_tput: dict[str, float | None] = {}
    for r in path_b:
        name = r.get("model", "")
        if r.get("note"):          # stub result — skip
            continue
        if r.get("strategy") == "embedding":
            llm_sep[name]  = r.get("separation")
            llm_tput[name] = r.get("throughput_cards_per_sec")
        elif r.get("strategy") == "logit" and name not in llm_tput:
            llm_sep[name]  = None   # logit doesn't give separation
            llm_tput[name] = r.get("throughput_pairs_per_sec")

    for name in set(llm_sep) | set(llm_tput) | set(llm_retrain_hours) | set(llm_qualitative) | set(llm_complexity):
        rows[name] = {
            "separation":    llm_sep.get(name),
            "throughput":    llm_tput.get(name),
            "retrain_hours": llm_retrain_hours.get(name),
            "qualitative":   llm_qualitative.get(name),
            "complexity":    llm_complexity.get(name),
            "path":          "B",
        }

    return list(rows.keys()), rows


def compute_weighted_scores(
    model_names: list[str],
    rows: dict[str, dict],
) -> dict[str, float | None]:
    """
    Returns {model: weighted_score | None} — None if any criterion is missing.
    """
    # Build normalised columns
    norm: dict[str, dict[str, float | None]] = {}
    for key, _, _ in CRITERIA:
        raw = {m: rows[m].get(key) for m in model_names}
        higher = key not in ("retrain_hours", "complexity")
        norm[key] = normalise_column(raw, higher_is_better=higher)

    weighted: dict[str, float | None] = {}
    for m in model_names:
        total, weight_sum = 0.0, 0.0
        complete = True
        for key, weight, _ in CRITERIA:
            v = norm[key].get(m)
            if v is None:
                complete = False
                break
            total += weight * v
            weight_sum += weight
        weighted[m] = round(total / weight_sum, 4) if (complete and weight_sum > 0) else None

    return weighted


def print_matrix(
    model_names: list[str],
    rows: dict[str, dict],
    weighted: dict[str, float | None],
) -> None:
    col_w = 13
    header_cells = ["Model", "Path"] + [label for _, _, label in CRITERIA] + ["Weighted"]
    widths = [14, 5] + [max(col_w, len(label)) for _, _, label in CRITERIA] + [9]

    def fmt_row(cells):
        return "  ".join(str(c).ljust(w) for c, w in zip(cells, widths))

    sep = "─" * sum(w + 2 for w in widths)
    print(f"\n{sep}")
    print(fmt_row(header_cells))
    print(fmt_row(["", "(wt)"] + [f"({int(w*100)}%)" for _, w, _ in CRITERIA] + [""]))
    print(sep)

    for m in model_names:
        r = rows[m]
        cells = [m, r["path"]]
        for key, _, _ in CRITERIA:
            v = r.get(key)
            if v is None:
                cells.append("—")
            elif key == "throughput":
                cells.append(f"{v:.0f}/s")
            elif key == "retrain_hours":
                cells.append(f"{v:.0f} h")
            elif key in ("qualitative", "complexity"):
                cells.append(f"{v:.0f}/5")
            else:
                cells.append(f"{v:.4f}")
        w = weighted.get(m)
        cells.append(f"{w:.3f}" if w is not None else "—")
        print(fmt_row(cells))

    print(sep)

    complete = {m: v for m, v in weighted.items() if v is not None}
    if complete:
        best = max(complete, key=complete.get)
        print(f"\nTop model (complete rows only): {best}  score={complete[best]:.3f}\n")
    else:
        print("\nNo model has all criteria filled in yet.\n")


def main():
    parser = argparse.ArgumentParser(description="Render spike #46 decision matrix")
    parser.add_argument("--retrain-hours",   nargs="+", metavar="MODEL=HRS",
                        help="Estimated re-train hours per Path A model")
    parser.add_argument("--qualitative",     nargs="+", metavar="MODEL=SCORE",
                        help="Qualitative top-10 score (1–5) per Path A model")
    parser.add_argument("--complexity",      nargs="+", metavar="MODEL=SCORE",
                        help="Operational complexity (1–5) per Path A model")
    parser.add_argument("--llm-retrain-hours", nargs="+", metavar="MODEL=HRS")
    parser.add_argument("--llm-qualitative",   nargs="+", metavar="MODEL=SCORE")
    parser.add_argument("--llm-complexity",    nargs="+", metavar="MODEL=SCORE")
    parser.add_argument("--save", action="store_true",
                        help="Save the current matrix to spike/results/decision_matrix.json")
    args = parser.parse_args()

    path_a = load_path_a()
    path_b = load_path_b()

    if not path_a and not path_b:
        print("No results found yet.  Run bench_encoders.py and/or bench_llm.py first.")
        return

    model_names, rows = build_matrix(
        path_a, path_b,
        retrain_hours     = _parse_kv(args.retrain_hours),
        qualitative       = _parse_kv(args.qualitative),
        complexity        = _parse_kv(args.complexity),
        llm_retrain_hours = _parse_kv(args.llm_retrain_hours),
        llm_qualitative   = _parse_kv(args.llm_qualitative),
        llm_complexity    = _parse_kv(args.llm_complexity),
    )

    weighted = compute_weighted_scores(model_names, rows)
    print_matrix(model_names, rows, weighted)

    if args.save:
        out = {
            "models":   model_names,
            "rows":     rows,
            "weighted": weighted,
        }
        p = RESULTS_DIR / "decision_matrix.json"
        p.write_text(json.dumps(out, indent=2))
        print(f"Saved → {p}")


if __name__ == "__main__":
    main()
