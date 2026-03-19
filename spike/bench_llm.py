"""
Spike #46 — Step 3: Path B LLM pairwise-scoring benchmark.

Tests whether a quantized 7B LLM can score card-pair synergy well enough
to replace (or enrich) the heuristic synergy_edges pipeline.

Two scoring strategies are evaluated side-by-side:

  logit     Extract the log-probability of the first "Yes" vs "No" token
            from a prompted completion.  Fast; works with any CausalLM.

  embedding Use the last-hidden-state of the [EOS] token as a card
            representation, then compute cosine similarity exactly as in
            Path A.  Lets us compare a 7B embedding directly against
            the sentence-encoder baseline.

Candidate models (quantized via bitsandbytes or llama.cpp GGUF)
----------------------------------------------------------------
  mistral-7b    mistralai/Mistral-7B-Instruct-v0.3   (HF Hub, bfloat16 / 4-bit)
  llama3-8b     meta-llama/Meta-Llama-3-8B-Instruct  (HF Hub, bfloat16 / 4-bit)

For CPU-only testing a lightweight stub model can be used with --stub:
  flan-t5-small  google/flan-t5-small  (seq2seq; validates harness logic only)

Outputs
-------
  spike/results/path_b.json   machine-readable results
  Printed table               human-readable summary

Usage
-----
    # Full GPU run (after 5060 Ti arrives):
    python spike/bench_llm.py --model mistral-7b --quant 4bit
    python spike/bench_llm.py --model llama3-8b  --quant 4bit

    # Harness validation on CPU (no GPU required):
    python spike/bench_llm.py --stub --pairs 50
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import numpy as np

DATA_DIR    = Path(__file__).parent / "data"
RESULTS_DIR = Path(__file__).parent / "results"

CANDIDATE_MODELS = {
    "mistral-7b": "mistralai/Mistral-7B-Instruct-v0.3",
    "llama3-8b":  "meta-llama/Meta-Llama-3-8B-Instruct",
    # lightweight stub for harness validation only
    "flan-t5-small": "google/flan-t5-small",
}

# ── Prompt ────────────────────────────────────────────────────────────────────

SYNERGY_PROMPT_TEMPLATE = """\
You are an expert Magic: The Gathering Commander deck builder.

Card A oracle text:
{oracle_a}

Card B oracle text:
{oracle_b}

Do Card A and Card B synergize in a Commander deck? Answer with a single word: Yes or No."""


def build_prompt(oracle_a: str, oracle_b: str) -> str:
    return SYNERGY_PROMPT_TEMPLATE.format(
        oracle_a=oracle_a.strip() or "(no oracle text)",
        oracle_b=oracle_b.strip() or "(no oracle text)",
    )


# ── Logit-based scorer ────────────────────────────────────────────────────────

def score_pair_logit(
    model,
    tokenizer,
    oracle_a: str,
    oracle_b: str,
    yes_ids: list[int],
    no_ids: list[int],
    device: Any,
) -> float:
    """
    Return P(Yes) / (P(Yes) + P(No)) by comparing the summed log-probs
    of the 'Yes' and 'No' token sets at the first generated position.
    Returns a float in [0, 1]; > 0.5 means the model predicts synergy.
    """
    import torch

    prompt = build_prompt(oracle_a, oracle_b)
    inputs = tokenizer(prompt, return_tensors="pt").to(device)

    with torch.no_grad():
        outputs = model(**inputs)

    logits = outputs.logits[0, -1, :]          # (vocab,)
    log_probs = torch.log_softmax(logits, dim=-1)

    yes_lp = torch.logsumexp(log_probs[yes_ids], dim=0).item()
    no_lp  = torch.logsumexp(log_probs[no_ids],  dim=0).item()

    # Convert to probability
    yes_p = np.exp(yes_lp)
    no_p  = np.exp(no_lp)
    total = yes_p + no_p
    return yes_p / total if total > 0 else 0.5


def get_yes_no_ids(tokenizer) -> tuple[list[int], list[int]]:
    """
    Collect all token IDs whose decoded string starts with 'yes' or 'no'
    (case-insensitive, with and without leading space).
    """
    vocab = tokenizer.get_vocab()
    yes_ids, no_ids = [], []
    for token, idx in vocab.items():
        decoded = tokenizer.decode([idx]).strip().lower()
        if decoded.startswith("yes"):
            yes_ids.append(idx)
        elif decoded.startswith("no"):
            no_ids.append(idx)
    return yes_ids, no_ids


# ── Embedding-based scorer ────────────────────────────────────────────────────

def embed_cards_llm(
    model,
    tokenizer,
    cards: list[dict],
    device: Any,
    batch_size: int = 4,
) -> tuple[dict[str, np.ndarray], float]:
    """
    Encode each card's oracle_text via the LLM's last hidden state (EOS token).
    Returns ({card_id: unit_vector}, cards_per_second).
    """
    import torch

    embeddings: dict[str, np.ndarray] = {}
    t0 = time.perf_counter()

    for i in range(0, len(cards), batch_size):
        batch = cards[i : i + batch_size]
        texts = [c.get("oracle_text") or "" for c in batch]
        ids   = [c["id"] for c in batch]

        enc = tokenizer(
            texts,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=512,
        ).to(device)

        with torch.no_grad():
            out = model(**enc, output_hidden_states=True)

        # Last hidden state at the final (non-padding) token position
        hidden = out.hidden_states[-1]  # (batch, seq, dim)
        attention_mask = enc["attention_mask"]
        # Index of last real token per example
        last_pos = attention_mask.sum(dim=1) - 1  # (batch,)

        for j, card_id in enumerate(ids):
            vec = hidden[j, last_pos[j], :].float().cpu().numpy()
            norm = np.linalg.norm(vec)
            embeddings[card_id] = vec / norm if norm > 0 else vec

    elapsed = time.perf_counter() - t0
    return embeddings, len(cards) / elapsed


# ── Evaluation helpers ────────────────────────────────────────────────────────

def evaluate_logit_classifier(
    scores: list[float],
    labels: list[int],
    threshold: float = 0.5,
) -> dict:
    preds = [1 if s >= threshold else 0 for s in scores]
    tp = sum(p == 1 and l == 1 for p, l in zip(preds, labels))
    fp = sum(p == 1 and l == 0 for p, l in zip(preds, labels))
    fn = sum(p == 0 and l == 1 for p, l in zip(preds, labels))
    tn = sum(p == 0 and l == 0 for p, l in zip(preds, labels))
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1        = (2 * precision * recall / (precision + recall)
                 if (precision + recall) > 0 else 0.0)
    accuracy  = (tp + tn) / len(labels) if labels else 0.0
    return {"accuracy": accuracy, "precision": precision, "recall": recall, "f1": f1,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def evaluate_embedding_separation(
    embeddings: dict[str, np.ndarray],
    pairs: list[dict],
) -> tuple[float, float, float]:
    pos_sims, neg_sims = [], []
    for p in pairs:
        a = embeddings.get(p["card_a"])
        b = embeddings.get(p["card_b"])
        if a is None or b is None:
            continue
        sim = float(np.dot(a, b))
        if p["label"] == 1:
            pos_sims.append(sim)
        else:
            neg_sims.append(sim)
    mean_pos = float(np.mean(pos_sims)) if pos_sims else 0.0
    mean_neg = float(np.mean(neg_sims)) if neg_sims else 0.0
    return mean_pos, mean_neg, mean_pos - mean_neg


# ── Stub mode (CPU validation) ────────────────────────────────────────────────

def run_stub(pairs: list[dict], n: int) -> dict:
    """
    Validate harness logic without a real LLM.
    Uses random scores to confirm the pipeline runs end-to-end.
    """
    print("\n[STUB MODE] Validating harness with random scores (no real model)…")
    pairs = pairs[:n]
    scores = [np.random.random() for _ in pairs]
    labels = [p["label"] for p in pairs]
    metrics = evaluate_logit_classifier(scores, labels)
    print(f"  Random-baseline accuracy: {metrics['accuracy']:.3f}  F1: {metrics['f1']:.3f}")
    print("  Harness OK — swap in a real model with --model <name>")
    return {
        "model": "stub (random)",
        "strategy": "logit",
        "pairs_evaluated": len(pairs),
        "metrics": metrics,
        "throughput_pairs_per_sec": None,
        "device": "cpu",
        "note": "stub validation only — not a real result",
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Path B LLM synergy-scoring benchmark")
    parser.add_argument(
        "--model",
        choices=list(CANDIDATE_MODELS),
        default=None,
        help="Model to benchmark (required unless --stub)",
    )
    parser.add_argument(
        "--strategy",
        choices=["logit", "embedding", "both"],
        default="both",
        help="Scoring strategy to evaluate (default: both)",
    )
    parser.add_argument(
        "--quant",
        choices=["none", "4bit", "8bit"],
        default="none",
        help="Quantization level via bitsandbytes (default: none). "
             "4bit / 8bit require GPU + bitsandbytes installed.",
    )
    parser.add_argument("--stub", action="store_true",
                        help="Run harness validation with random scores (no GPU needed)")
    parser.add_argument("--pairs", type=int, default=None,
                        help="Max pairs to evaluate (default: all)")
    parser.add_argument("--batch-size", type=int, default=4,
                        help="Batch size for embedding strategy (default: 4)")
    args = parser.parse_args()

    if not args.stub and args.model is None:
        parser.error("--model is required unless --stub is set")

    pairs_path = DATA_DIR / "eval_pairs.json"
    cards_path = DATA_DIR / "card_sample.json"
    if not pairs_path.exists():
        raise FileNotFoundError("Run prepare_eval_data.py first.")

    pairs = json.loads(pairs_path.read_text())
    cards = json.loads(cards_path.read_text()) if cards_path.exists() else []
    card_text: dict[str, str] = {c["id"]: c.get("oracle_text") or "" for c in cards}

    if args.pairs:
        pairs = pairs[: args.pairs]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    if args.stub:
        result = run_stub(pairs, args.pairs or len(pairs))
        _save_result(result)
        return

    import torch
    model_id = CANDIDATE_MODELS[args.model]
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")
    print(f"Loading {args.model} ({model_id})…")

    load_kwargs: dict = {"device_map": "auto"}
    if args.quant == "4bit":
        from transformers import BitsAndBytesConfig
        load_kwargs["quantization_config"] = BitsAndBytesConfig(load_in_4bit=True)
    elif args.quant == "8bit":
        from transformers import BitsAndBytesConfig
        load_kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)

    from transformers import AutoModelForCausalLM, AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(model_id)
    model = AutoModelForCausalLM.from_pretrained(model_id, **load_kwargs)
    model.eval()

    results_this_run = []

    # ── Logit strategy ────────────────────────────────────────────────────────
    if args.strategy in ("logit", "both"):
        print(f"\nStrategy: logit  ({len(pairs)} pairs)…")
        yes_ids, no_ids = get_yes_no_ids(tokenizer)
        scores, labels = [], []
        t0 = time.perf_counter()

        for i, pair in enumerate(pairs):
            oracle_a = card_text.get(pair["card_a"], "")
            oracle_b = card_text.get(pair["card_b"], "")
            s = score_pair_logit(model, tokenizer, oracle_a, oracle_b,
                                 yes_ids, no_ids, device)
            scores.append(s)
            labels.append(pair["label"])
            if (i + 1) % 50 == 0:
                print(f"  {i + 1}/{len(pairs)}…")

        elapsed = time.perf_counter() - t0
        throughput = len(pairs) / elapsed
        metrics = evaluate_logit_classifier(scores, labels)
        print(f"  accuracy: {metrics['accuracy']:.3f}  F1: {metrics['f1']:.3f}  "
              f"throughput: {throughput:.1f} pairs/sec")

        results_this_run.append({
            "model": args.model,
            "model_id": model_id,
            "strategy": "logit",
            "quant": args.quant,
            "pairs_evaluated": len(pairs),
            "metrics": metrics,
            "throughput_pairs_per_sec": throughput,
            "device": str(device),
        })

    # ── Embedding strategy ────────────────────────────────────────────────────
    if args.strategy in ("embedding", "both"):
        print(f"\nStrategy: embedding  ({len(cards)} cards)…")
        # Only encode cards that appear in the pairs we're evaluating
        needed_ids = {p["card_a"] for p in pairs} | {p["card_b"] for p in pairs}
        subset = [c for c in cards if c["id"] in needed_ids]
        print(f"  encoding {len(subset)} cards (batch={args.batch_size})…")

        embeddings, throughput_cards = embed_cards_llm(
            model, tokenizer, subset, device, args.batch_size
        )
        mean_pos, mean_neg, separation = evaluate_embedding_separation(embeddings, pairs)
        print(f"  pos sim: {mean_pos:.4f}  neg sim: {mean_neg:.4f}  "
              f"separation: {separation:.4f}  throughput: {throughput_cards:.1f} cards/sec")

        results_this_run.append({
            "model": args.model,
            "model_id": model_id,
            "strategy": "embedding",
            "quant": args.quant,
            "cards_encoded": len(subset),
            "mean_pos_sim": mean_pos,
            "mean_neg_sim": mean_neg,
            "separation": separation,
            "throughput_cards_per_sec": throughput_cards,
            "device": str(device),
        })

    for r in results_this_run:
        _save_result(r)

    print(f"\nResults saved → {RESULTS_DIR / 'path_b.json'}")


def _save_result(result: dict) -> None:
    out_path = RESULTS_DIR / "path_b.json"
    existing: list[dict] = []
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text())
        except json.JSONDecodeError:
            pass
    # Deduplicate by (model, strategy, quant)
    key = (result.get("model"), result.get("strategy"), result.get("quant"))
    merged = [r for r in existing
              if (r.get("model"), r.get("strategy"), r.get("quant")) != key]
    merged.append(result)
    out_path.write_text(json.dumps(merged, indent=2))


if __name__ == "__main__":
    main()
