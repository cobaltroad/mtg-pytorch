"""
Spike #46 — Step 2: Path A encoder benchmark.

Evaluates four sentence-encoder candidates on the fixed eval dataset
produced by prepare_eval_data.py.  Measures two things:

  separation   mean cosine-sim(positive pairs) − mean cosine-sim(negative pairs)
               Higher is better — the model more clearly distinguishes
               synergistic from random card pairs.

  throughput   cards encoded per second on the current hardware.
               Run before and after GPU arrives to get both CPU and GPU numbers.

Candidate models
----------------
  MiniLM-L6-v2          384-d   current baseline
  all-mpnet-base-v2     768-d   Path A candidate
  gte-large             1024-d  Path A candidate
  e5-large-v2           1024-d  Path A candidate (uses symmetric "query: " prefix)
  bge-base-en-v1.5      768-d   Path A candidate (BAAI BGE base)
  bge-large-en-v1.5     1024-d  Path A candidate (BAAI BGE large)
  nomic-embed-text-v1.5 768-d   Path A candidate (Matryoshka; requires trust_remote_code)

Outputs
-------
  spike/results/path_a.json   machine-readable results
  Printed table               human-readable summary

Usage
-----
    python spike/bench_encoders.py
    python spike/bench_encoders.py --models MiniLM all-mpnet   # subset
    python spike/bench_encoders.py --batch-size 64
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import common  # noqa: F401 — loads .env at import time
import numpy as np

DATA_DIR = Path(__file__).parent / "data"
RESULTS_DIR = Path(__file__).parent / "results"

# (short_name, hf_model_id, query_prefix, trust_remote_code)
CANDIDATE_MODELS: list[tuple[str, str, str, bool]] = [
    ("MiniLM",      "sentence-transformers/all-MiniLM-L6-v2",    "",        False),
    ("all-mpnet",   "sentence-transformers/all-mpnet-base-v2",    "",        False),
    ("gte-large",   "thenlper/gte-large",                         "",        False),
    # e5 models require a prefix; use symmetric "query: " for similarity (not retrieval)
    ("e5-large",    "intfloat/e5-large-v2",                       "query: ", False),
    ("bge-base",    "BAAI/bge-base-en-v1.5",                      "",        False),
    ("bge-large",   "BAAI/bge-large-en-v1.5",                     "",        False),
    ("nomic",       "nomic-ai/nomic-embed-text-v1.5",             "search_document: ", True),
]

MODEL_SHORT_NAMES = {m[0] for m in CANDIDATE_MODELS}


def load_data() -> tuple[list[dict], list[dict]]:
    card_path = DATA_DIR / "card_sample.json"
    pairs_path = DATA_DIR / "eval_pairs.json"
    if not card_path.exists() or not pairs_path.exists():
        raise FileNotFoundError(
            "Run prepare_eval_data.py first to generate spike/data/ files."
        )
    cards = json.loads(card_path.read_text())
    pairs = json.loads(pairs_path.read_text())
    return cards, pairs


def encode_cards(
    model,
    cards: list[dict],
    prefix: str,
    batch_size: int,
) -> tuple[dict[str, np.ndarray], float]:
    """
    Encode all cards.  Returns ({card_id: unit_vector}, cards_per_second).
    """
    texts = [prefix + (c.get("oracle_text") or "") for c in cards]
    ids = [c["id"] for c in cards]

    t0 = time.perf_counter()
    vecs = model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    elapsed = time.perf_counter() - t0

    throughput = len(cards) / elapsed
    return {ids[i]: vecs[i] for i in range(len(ids))}, throughput


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    # Vectors are already L2-normalised by encode()
    return float(np.dot(a, b))


def evaluate_separation(
    embeddings: dict[str, np.ndarray],
    pairs: list[dict],
) -> tuple[float, float, float]:
    """
    Returns (mean_pos_sim, mean_neg_sim, separation).
    Pairs where either card is missing from embeddings are skipped.
    """
    pos_sims, neg_sims = [], []
    skipped = 0
    for p in pairs:
        a_vec = embeddings.get(p["card_a"])
        b_vec = embeddings.get(p["card_b"])
        if a_vec is None or b_vec is None:
            skipped += 1
            continue
        sim = cosine_sim(a_vec, b_vec)
        if p["label"] == 1:
            pos_sims.append(sim)
        else:
            neg_sims.append(sim)

    if skipped:
        print(f"    (skipped {skipped} pairs — cards not in sample)")

    mean_pos = float(np.mean(pos_sims)) if pos_sims else 0.0
    mean_neg = float(np.mean(neg_sims)) if neg_sims else 0.0
    return mean_pos, mean_neg, mean_pos - mean_neg


def run_model(
    short_name: str,
    model_id: str,
    prefix: str,
    cards: list[dict],
    pairs: list[dict],
    batch_size: int,
    trust_remote_code: bool = False,
) -> dict:
    from sentence_transformers import SentenceTransformer

    print(f"\n[{short_name}]  loading {model_id} …")
    model = SentenceTransformer(model_id, trust_remote_code=trust_remote_code)
    dim = model.get_sentence_embedding_dimension()
    print(f"  dimension: {dim}")

    print(f"  encoding {len(cards)} cards (batch={batch_size})…")
    embeddings, throughput = encode_cards(model, cards, prefix, batch_size)
    print(f"  throughput: {throughput:.1f} cards/sec")

    print(f"  evaluating separation on {len(pairs)} pairs…")
    mean_pos, mean_neg, separation = evaluate_separation(embeddings, pairs)
    print(f"  pos sim: {mean_pos:.4f}  neg sim: {mean_neg:.4f}  separation: {separation:.4f}")

    return {
        "model": short_name,
        "model_id": model_id,
        "dim": dim,
        "mean_pos_sim": mean_pos,
        "mean_neg_sim": mean_neg,
        "separation": separation,
        "throughput_cards_per_sec": throughput,
        "device": _device_name(model),
    }


def _device_name(model) -> str:
    try:
        return str(next(model.parameters()).device)
    except Exception:
        return "unknown"


def print_results_table(results: list[dict]) -> None:
    header = f"{'Model':<14} {'Dim':>4}  {'Pos sim':>7}  {'Neg sim':>7}  {'Separation':>10}  {'Throughput':>13}  Device"
    sep    = "-" * len(header)
    print(f"\n{sep}")
    print(header)
    print(sep)
    for r in results:
        print(
            f"{r['model']:<14} {r['dim']:>4}  "
            f"{r['mean_pos_sim']:>7.4f}  {r['mean_neg_sim']:>7.4f}  "
            f"{r['separation']:>10.4f}  "
            f"{r['throughput_cards_per_sec']:>10.1f}/s  "
            f"{r['device']}"
        )
    print(sep)
    best = max(results, key=lambda r: r["separation"])
    print(f"\nBest separation: {best['model']} ({best['separation']:.4f})\n")


def main():
    parser = argparse.ArgumentParser(description="Path A encoder benchmark")
    parser.add_argument(
        "--models",
        nargs="+",
        choices=list(MODEL_SHORT_NAMES),
        default=None,
        help="Subset of models to run (default: all)",
    )
    parser.add_argument("--batch-size", type=int, default=128,
                        help="Encoding batch size (default: 128)")
    args = parser.parse_args()

    selected = set(args.models) if args.models else MODEL_SHORT_NAMES

    print("Loading eval data…")
    cards, pairs = load_data()
    n_pos = sum(1 for p in pairs if p["label"] == 1)
    n_neg = sum(1 for p in pairs if p["label"] == 0)
    print(f"  {len(cards)} cards, {n_pos} positive pairs, {n_neg} negative pairs")

    results = []
    for short_name, model_id, prefix, trust_remote_code in CANDIDATE_MODELS:
        if short_name not in selected:
            continue
        result = run_model(short_name, model_id, prefix, cards, pairs, args.batch_size,
                           trust_remote_code=trust_remote_code)
        results.append(result)

    print_results_table(results)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / "path_a.json"

    # Merge with any existing results so successive runs (CPU then GPU) accumulate
    existing: list[dict] = []
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text())
        except json.JSONDecodeError:
            pass

    # Replace entries for models we just ran, keep others
    run_names = {r["model"] for r in results}
    merged = [r for r in existing if r["model"] not in run_names] + results
    out_path.write_text(json.dumps(merged, indent=2))
    print(f"Results saved -> {out_path}")


if __name__ == "__main__":
    main()
