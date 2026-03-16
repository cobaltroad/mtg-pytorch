"""
Phase 4 deck construction evaluator.

Measures how well DeckConstructor ranks true deck cards above random candidates,
and produces a qualitative top-N card list for a given commander.

Metrics
-------
Recall@K  — fraction of held-out positions where the true card appears in top-K
MRR       — mean reciprocal rank of the true card across all held-out positions

Usage
-----
    # Quantitative: Recall@K and MRR across all decks in DB
    python eval_deck.py --mode recall

    # Qualitative: top-N cards for a specific commander
    python eval_deck.py --mode topn --commander "Wilhelt, the Rotcleaver"
    python eval_deck.py --mode topn --commander "The Ur-Dragon" --top 30

    # Both
    python eval_deck.py --mode both --commander "Wilhelt, the Rotcleaver"

Run from inside the trainer container:
    docker compose run --rm trainer python eval_deck.py --mode both --commander "Wilhelt, the Rotcleaver"
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from pathlib import Path

import numpy as np
import psycopg2
import psycopg2.extras
import torch
import torch.nn.functional as F

from train import (
    CardEncoder,
    DeckConstructor,
    load_checkpoint,
    load_embeddings,
    load_decks,
    CHECKPOINT_DIR,
    DATABASE_URL,
    EMBEDDING_MODEL,
)


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def load_card_meta() -> dict[str, dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("SELECT id::text, name, type_line, mana_cost FROM cards")
            return {r["id"]: dict(r) for r in cur.fetchall()}


def find_card(query: str, meta: dict[str, dict]) -> str | None:
    q = query.lower()
    for card_id, info in meta.items():
        if info["name"].lower() == q:
            return card_id
    for card_id, info in meta.items():
        if info["name"].lower().startswith(q):
            return card_id
    for card_id, info in meta.items():
        if q in info["name"].lower():
            return card_id
    return None


def score_candidates(
    model: DeckConstructor,
    cmd_raw: torch.Tensor,       # (384,)
    context_raw: torch.Tensor,   # (pos, 384)
    candidate_raw: torch.Tensor, # (C, 384)
    device: torch.device,
) -> torch.Tensor:
    """Return scores (C,) for each candidate given commander + context."""
    model.eval()
    with torch.no_grad():
        z_cmd       = model.card_encoder(cmd_raw.unsqueeze(0).to(device))          # (1, 256)
        z_context   = model.card_encoder(context_raw.to(device)).unsqueeze(0)     # (1, pos, 256)
        z_candidates = model.card_encoder(candidate_raw.to(device)).unsqueeze(0)  # (1, C, 256)
        scores = model(z_cmd, z_context, z_candidates).squeeze(0)                 # (C,)
    return scores.cpu()


def eval_recall(
    model: DeckConstructor,
    decks: list[dict],
    embeddings: dict[str, np.ndarray],
    all_ids: list[str],
    device: torch.device,
    n_neg: int = 99,
    positions_per_deck: int = 10,
    ks: tuple[int, ...] = (1, 5, 10, 20, 50),
) -> None:
    """Compute Recall@K and MRR across held-out deck positions."""
    all_raw = torch.from_numpy(
        np.stack([embeddings[k] for k in all_ids]).astype(np.float32)
    )

    hits = {k: 0 for k in ks}
    reciprocal_ranks: list[float] = []
    total = 0

    for deck in decks:
        cmd_id   = deck["commander_id"]
        card_ids = deck["card_ids"]
        K        = len(card_ids)
        if K < 2:
            continue

        cmd_raw  = torch.from_numpy(embeddings[cmd_id].astype(np.float32))
        card_raw = torch.from_numpy(
            np.stack([embeddings[c] for c in card_ids]).astype(np.float32)
        )

        legal_pool = deck["legal_neg_indices"]
        n_pos = min(positions_per_deck, K - 1)
        positions = random.sample(range(1, K), n_pos)

        for pos in positions:
            context_raw = card_raw[:pos]          # (pos, 384)
            target_raw  = card_raw[pos]           # (384,)

            # Sample n_neg negatives from color-legal pool (exclude the true card)
            chosen  = np.random.choice(legal_pool, size=n_neg * 2, replace=True)
            neg_ids = [all_ids[i] for i in chosen if all_ids[i] != card_ids[pos]][:n_neg]
            if len(neg_ids) < n_neg:
                continue

            neg_raw = torch.from_numpy(
                np.stack([embeddings[i] for i in neg_ids]).astype(np.float32)
            )

            # Candidates: true card at index 0, then negatives
            candidate_raw = torch.cat([target_raw.unsqueeze(0), neg_raw], dim=0)  # (1+n_neg, 384)
            scores = score_candidates(model, cmd_raw, context_raw, candidate_raw, device)

            # Rank of the true card (index 0) among all candidates
            rank = int((scores > scores[0]).sum().item()) + 1  # 1-indexed

            for k in ks:
                if rank <= k:
                    hits[k] += 1
            reciprocal_ranks.append(1.0 / rank)
            total += 1

    if total == 0:
        print("No positions evaluated.")
        return

    print(f"\n{'─' * 50}")
    print(f"  Recall@K  ({total} positions, {n_neg} negatives each)")
    print(f"{'─' * 50}")
    for k in ks:
        print(f"  Recall@{k:<3}  {hits[k]/total:.3f}  ({hits[k]}/{total})")
    mrr = sum(reciprocal_ranks) / len(reciprocal_ranks)
    print(f"  MRR        {mrr:.4f}")
    print(f"  Random @1  {1/(n_neg+1):.4f}  (baseline)")
    print()


def eval_topn(
    model: DeckConstructor,
    commander_name: str,
    embeddings: dict[str, np.ndarray],
    all_ids: list[str],
    meta: dict[str, dict],
    device: torch.device,
    top: int = 30,
    context_size: int = 5,
) -> None:
    """Score all color-legal cards for a commander and print the top-N."""
    cmd_id = find_card(commander_name, meta)
    if cmd_id is None:
        print(f"ERROR: No card found matching '{commander_name}'", file=sys.stderr)
        return
    if cmd_id not in embeddings:
        print(f"ERROR: No embedding for '{commander_name}'", file=sys.stderr)
        return

    cmd_meta = meta[cmd_id]
    print(f"\nCommander  : {cmd_meta['name']}  [{cmd_meta.get('mana_cost','—')}]")
    print(f"Type       : {cmd_meta.get('type_line','—')}")
    print(f"Context    : empty deck (no cards in hand yet)")

    cmd_raw     = torch.from_numpy(embeddings[cmd_id].astype(np.float32))
    # Use a single placeholder token as the context (can't have empty sequence in decoder)
    context_raw = cmd_raw.unsqueeze(0)  # (1, 384) — commander as its own context seed

    # Score all cards except the commander itself in batches
    batch_size = 512
    all_scores: list[float] = []

    model.eval()
    with torch.no_grad():
        z_cmd     = model.card_encoder(cmd_raw.unsqueeze(0).to(device))       # (1, 256)
        z_context = model.card_encoder(context_raw.to(device)).unsqueeze(0)   # (1, 1, 256)

        for i in range(0, len(all_ids), batch_size):
            batch_raw = torch.from_numpy(
                np.stack([embeddings[k] for k in all_ids[i: i + batch_size]]).astype(np.float32)
            ).to(device)
            z_cands = model.card_encoder(batch_raw).unsqueeze(0)  # (1, B, 256)
            scores  = model(z_cmd, z_context, z_cands).squeeze(0) # (B,)
            all_scores.extend(scores.cpu().tolist())

    ranked = sorted(
        [
            {**meta.get(all_ids[i], {"name": all_ids[i]}), "score": all_scores[i]}
            for i in range(len(all_ids))
            if all_ids[i] != cmd_id and all_ids[i] in meta
        ],
        key=lambda r: r["score"],
        reverse=True,
    )

    print(f"\n{'─' * 72}")
    print(f"  Top {top} cards for: {cmd_meta['name']}")
    print(f"{'─' * 72}")
    print(f"  {'#':>3}  {'Score':>7}  {'Name':<32}  {'Type'}")
    print(f"  {'─'*3}  {'─'*7}  {'─'*32}  {'─'*25}")
    for i, row in enumerate(ranked[:top], 1):
        name  = row["name"][:31]
        ttype = (row.get("type_line") or "")[:25]
        print(f"  {i:>3}  {row['score']:>7.3f}  {name:<32}  {ttype}")
    print()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["recall", "topn", "both"], default="both")
    parser.add_argument("--commander", default="Wilhelt, the Rotcleaver",
                        help="Commander name for topn/both modes")
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--n-neg", type=int, default=99,
                        help="Number of random negatives per recall position")
    parser.add_argument("--positions", type=int, default=10,
                        help="Positions sampled per deck for recall eval")
    parser.add_argument("--checkpoint", default="phase4_best")
    args = parser.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    print("Loading card metadata…")
    meta = load_card_meta()
    print("Loading embeddings…")
    embeddings = load_embeddings()
    all_ids = list(embeddings.keys())

    print(f"Loading checkpoint '{args.checkpoint}'…")
    model = DeckConstructor().to(device)
    load_checkpoint(model, args.checkpoint, device)

    if args.mode in ("recall", "both"):
        print("Loading decks for recall eval…")
        decks = load_decks(embeddings)
        eval_recall(model, decks, embeddings, all_ids, device,
                    n_neg=args.n_neg, positions_per_deck=args.positions)

    if args.mode in ("topn", "both"):
        eval_topn(model, args.commander, embeddings, all_ids, meta, device, top=args.top)


if __name__ == "__main__":
    main()
