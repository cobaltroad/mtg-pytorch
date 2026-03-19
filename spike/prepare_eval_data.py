"""
Spike #46 — Step 1: Prepare evaluation datasets.

Extracts two fixed datasets from the DB and saves them as JSON so every
benchmark script uses identical ground truth regardless of when it runs.

Outputs
-------
spike/data/card_sample.json   — 1 000 cards with oracle_text, for encoding
spike/data/eval_pairs.json    — 500 positive + 500 negative synergy pairs

Usage
-----
    python spike/prepare_eval_data.py
    python spike/prepare_eval_data.py --sample 1000 --pairs 500
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL", "")
DATA_DIR = Path(__file__).parent / "data"


def get_conn():
    if not DATABASE_URL:
        sys.exit("ERROR: DATABASE_URL is not set.")
    return psycopg2.connect(DATABASE_URL)


def extract_card_sample(cur, n: int) -> list[dict]:
    """Return n cards that have oracle_text and an existing embedding."""
    cur.execute(
        """
        SELECT c.id::text, c.name, c.oracle_text, c.type_line, c.mana_cost
        FROM cards c
        JOIN card_embeddings ce ON ce.card_id = c.id
        WHERE c.oracle_text IS NOT NULL
          AND c.oracle_text <> ''
        ORDER BY random()
        LIMIT %s
        """,
        (n,),
    )
    return [dict(r) for r in cur.fetchall()]


def extract_positive_pairs(cur, n: int) -> list[dict]:
    """
    Return n known-synergistic pairs from synergy_edges (ability_trigger only,
    score > 0.5).  Both cards must be in the card_sample set.
    """
    cur.execute(
        """
        SELECT se.card_a::text, se.card_b::text, se.score
        FROM synergy_edges se
        WHERE se.score_type = 'ability_trigger'
          AND se.score > 0.5
        ORDER BY random()
        LIMIT %s
        """,
        (n,),
    )
    return [
        {"card_a": r[0], "card_b": r[1], "score": float(r[2]), "label": 1}
        for r in cur.fetchall()
    ]


def extract_negative_pairs(
    cur,
    positive_set: set[tuple[str, str]],
    card_ids: list[str],
    n: int,
) -> list[dict]:
    """
    Sample n random card pairs that do NOT appear in synergy_edges.
    Pairs are drawn from card_ids to keep negatives within the card sample.
    """
    # Fetch all synergy_edge pairs (both directions) for the cards in our sample
    # to avoid accidentally labelling a real synergy as negative.
    placeholders = ",".join(["%s"] * len(card_ids))
    cur.execute(
        f"""
        SELECT card_a::text, card_b::text
        FROM synergy_edges
        WHERE card_a = ANY(%s::uuid[])
           OR card_b = ANY(%s::uuid[])
        """,
        (card_ids, card_ids),
    )
    known_synergies: set[tuple[str, str]] = set()
    for r in cur.fetchall():
        known_synergies.add((r[0], r[1]))
        known_synergies.add((r[1], r[0]))

    negatives: list[dict] = []
    attempts = 0
    max_attempts = n * 20
    ids = card_ids.copy()

    while len(negatives) < n and attempts < max_attempts:
        a, b = random.sample(ids, 2)
        if a == b:
            attempts += 1
            continue
        key = (min(a, b), max(a, b))
        if key in known_synergies or key in positive_set:
            attempts += 1
            continue
        known_synergies.add(key)
        negatives.append({"card_a": a, "card_b": b, "score": 0.0, "label": 0})
        attempts += 1

    if len(negatives) < n:
        print(
            f"  WARNING: only generated {len(negatives)}/{n} negatives "
            f"(card sample may be too small relative to synergy_edges coverage)"
        )
    return negatives


def main():
    parser = argparse.ArgumentParser(description="Prepare spike evaluation datasets")
    parser.add_argument("--sample", type=int, default=1000,
                        help="Number of cards in the fixed sample (default: 1000)")
    parser.add_argument("--pairs", type=int, default=500,
                        help="Number of positive (and negative) pairs (default: 500)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for reproducibility (default: 42)")
    args = parser.parse_args()

    random.seed(args.seed)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print("Connecting to database…")
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            print(f"Extracting {args.sample}-card sample…")
            cards = extract_card_sample(cur, args.sample)
            if not cards:
                sys.exit("ERROR: No cards with embeddings found. Run ingest first.")
            print(f"  {len(cards)} cards extracted.")

            card_sample_path = DATA_DIR / "card_sample.json"
            card_sample_path.write_text(json.dumps(cards, indent=2))
            print(f"  Saved → {card_sample_path}")

            print(f"Extracting {args.pairs} positive synergy pairs…")
            positives = extract_positive_pairs(cur, args.pairs)
            if not positives:
                sys.exit(
                    "ERROR: No synergy_edges with score_type='ability_trigger' found. "
                    "Run compute_synergy stage first."
                )
            print(f"  {len(positives)} positive pairs extracted.")

            card_ids = [c["id"] for c in cards]
            positive_set = {(min(p["card_a"], p["card_b"]), max(p["card_a"], p["card_b"]))
                            for p in positives}

            print(f"Sampling {args.pairs} negative pairs…")
            negatives = extract_negative_pairs(cur, positive_set, card_ids, args.pairs)
            print(f"  {len(negatives)} negative pairs generated.")

    eval_pairs = positives + negatives
    random.shuffle(eval_pairs)

    pairs_path = DATA_DIR / "eval_pairs.json"
    pairs_path.write_text(json.dumps(eval_pairs, indent=2))
    print(f"  Saved → {pairs_path}")

    print(
        f"\nDone.  {len(cards)} cards, "
        f"{len(positives)} positives, {len(negatives)} negatives."
    )
    print("Run bench_encoders.py next.")


if __name__ == "__main__":
    main()
