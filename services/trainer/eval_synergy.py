"""
Synergy sanity-check evaluator.

Given a card name, scores every other card in the embedding space using
the trained CardEncoder and prints the top-N most (and least) synergistic
cards according to the model.

Usage
-----
    python eval_synergy.py "Skullclamp"
    python eval_synergy.py "Atraxa, Praetors' Voice" --top 30
    python eval_synergy.py "Sol Ring" --checkpoint phase2_best --top 20

Run from inside the trainer container:
    docker compose run --rm trainer python eval_synergy.py "Skullclamp"
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import psycopg2
import psycopg2.extras
import torch

# Re-use model definition from train.py
from train import CardEncoder, load_checkpoint, CHECKPOINT_DIR, DATABASE_URL, EMBEDDING_MODEL


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def load_card_meta() -> dict[str, dict]:
    """Return {card_id: {name, type_line, mana_cost, oracle_text}} for all cards."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("""
                SELECT id::text, name, type_line, mana_cost, oracle_text
                FROM cards
            """)
            return {r["id"]: dict(r) for r in cur.fetchall()}


def load_embeddings(model_name: str = EMBEDDING_MODEL) -> dict[str, np.ndarray]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("""
                SELECT card_id::text, embedding
                FROM card_embeddings
                WHERE model = %s
            """, (model_name,))
            rows = cur.fetchall()

    result = {}
    for row in rows:
        vec = row["embedding"]
        if isinstance(vec, str):
            vec = np.fromstring(vec.strip("[]"), sep=",", dtype=np.float32)
        else:
            vec = np.array(vec, dtype=np.float32)
        result[row["card_id"]] = vec
    return result


def project_all(
    embeddings: dict[str, np.ndarray],
    model: CardEncoder,
    device: torch.device,
    batch_size: int = 512,
) -> tuple[list[str], np.ndarray]:
    """Run all embeddings through the CardEncoder. Returns (id_list, projected matrix)."""
    ids = list(embeddings.keys())
    raw = np.stack([embeddings[k] for k in ids])           # (N, 384)
    projected = []

    model.eval()
    with torch.no_grad():
        for i in range(0, len(ids), batch_size):
            batch = torch.from_numpy(raw[i: i + batch_size]).to(device)
            projected.append(model(batch).cpu().numpy())

    return ids, np.concatenate(projected, axis=0)           # (N, 256), L2-normalised


def find_card(query: str, meta: dict[str, dict]) -> str | None:
    """Return card_id for the best case-insensitive name match."""
    q = query.lower()
    # Exact match first
    for card_id, info in meta.items():
        if info["name"].lower() == q:
            return card_id
    # Prefix match
    for card_id, info in meta.items():
        if info["name"].lower().startswith(q):
            return card_id
    # Substring match
    for card_id, info in meta.items():
        if q in info["name"].lower():
            return card_id
    return None


def print_table(title: str, rows: list[dict], query_meta: dict) -> None:
    print(f"\n{'─' * 72}")
    print(f"  {title}")
    print(f"{'─' * 72}")
    print(f"  {'#':>3}  {'Score':>6}  {'Name':<30}  {'Type'}")
    print(f"  {'─'*3}  {'─'*6}  {'─'*30}  {'─'*25}")
    for i, row in enumerate(rows, 1):
        name = row["name"][:29]
        ttype = (row.get("type_line") or "")[:25]
        print(f"  {i:>3}  {row['score']:>6.3f}  {name:<30}  {ttype}")
    print()
    print(f"  Query card oracle text:")
    for line in (query_meta.get("oracle_text") or "(none)").split("\n"):
        print(f"    {line}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Evaluate synergy model for a given card")
    parser.add_argument("card", help="Card name (partial match ok)")
    parser.add_argument("--top", type=int, default=20, help="Number of top results to show")
    parser.add_argument("--bottom", type=int, default=5, help="Number of bottom results to show")
    parser.add_argument("--checkpoint", default="phase2_best", help="Checkpoint name")
    args = parser.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    print(f"Loading card metadata…")
    meta = load_card_meta()

    query_id = find_card(args.card, meta)
    if query_id is None:
        print(f"ERROR: No card found matching '{args.card}'", file=sys.stderr)
        sys.exit(1)

    query_meta = meta[query_id]
    print(f"Query card : {query_meta['name']}  [{query_meta.get('mana_cost','—')}]")
    print(f"Type       : {query_meta.get('type_line','—')}")

    print(f"Loading embeddings…")
    embeddings = load_embeddings()
    if query_id not in embeddings:
        print(f"ERROR: No embedding found for '{query_meta['name']}'", file=sys.stderr)
        sys.exit(1)

    print(f"Loading checkpoint '{args.checkpoint}'…")
    model = CardEncoder().to(device)
    load_checkpoint(model, args.checkpoint, device)

    print(f"Projecting {len(embeddings)} card embeddings…")
    all_ids, proj_matrix = project_all(embeddings, model, device)

    id_to_idx = {card_id: i for i, card_id in enumerate(all_ids)}
    q_vec = proj_matrix[id_to_idx[query_id]]           # (256,) already L2-normalised
    scores = proj_matrix @ q_vec                        # (N,) cosine similarities

    # Build ranked list, excluding the query card itself
    ranked = [
        {**meta.get(all_ids[i], {"name": all_ids[i]}), "score": float(scores[i])}
        for i in np.argsort(scores)[::-1]
        if all_ids[i] != query_id and all_ids[i] in meta
    ]

    print_table(
        f"Top {args.top} most synergistic with: {query_meta['name']}",
        ranked[: args.top],
        query_meta,
    )

    if args.bottom > 0:
        print_table(
            f"Bottom {args.bottom} least synergistic (sanity check)",
            ranked[-args.bottom :],
            query_meta,
        )


if __name__ == "__main__":
    main()
