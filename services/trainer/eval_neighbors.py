"""
Nearest-neighbour evaluator for Phase 1 checkpoints.

Loads a training artifact (.pt) and a Phase 1 checkpoint, projects all card
embeddings through the CardEncoder, then prints the top-N nearest neighbours
for a given card name.

Designed to run on the GPU machine with no database connection.

Usage
-----
    python eval_neighbors.py "Swords to Plowshares"
    python eval_neighbors.py "Llanowar Elves" --top 20
    python eval_neighbors.py "Swords to Plowshares" --checkpoint phase1_best --dataset .\\ingest_cache\\mtg_dataset.pt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from train import CardEncoder, load_artifact, load_checkpoint, CHECKPOINT_DIR


def load_card_meta_from_artifact(data: dict) -> dict[str, dict]:
    """Return {card_id: {name, mana_cost, type_line}} from artifact."""
    return data.get("card_meta", {})


def find_card(query: str, card_meta: dict[str, dict]) -> str | None:
    """Return card_id for the best case-insensitive name match."""
    q = query.lower()
    for card_id, info in card_meta.items():
        if info["name"].lower() == q:
            return card_id
    for card_id, info in card_meta.items():
        if info["name"].lower().startswith(q):
            return card_id
    for card_id, info in card_meta.items():
        if q in info["name"].lower():
            return card_id
    return None


def project_all(
    emb_matrix: np.ndarray,
    model: CardEncoder,
    device: torch.device,
    batch_size: int = 512,
) -> np.ndarray:
    """Project all raw embeddings through the CardEncoder. Returns (N, D) L2-normalised."""
    model.eval()
    projected = []
    with torch.no_grad():
        for i in range(0, len(emb_matrix), batch_size):
            batch = torch.from_numpy(emb_matrix[i: i + batch_size]).to(device)
            projected.append(model(batch).cpu().numpy())
    return np.concatenate(projected, axis=0)


def main():
    parser = argparse.ArgumentParser(
        description="Nearest-neighbour eval for Phase 1 checkpoints (no DB required)"
    )
    parser.add_argument("card", help="Card name to query (partial match ok)")
    parser.add_argument("--top", type=int, default=20, help="Number of neighbours to show")
    parser.add_argument(
        "--checkpoint", default="phase1_best",
        help="Checkpoint name in CHECKPOINT_DIR (default: phase1_best)",
    )
    parser.add_argument(
        "--dataset",
        default=str(Path(__file__).parent.parent.parent / "ingest_cache" / "mtg_dataset.pt"),
        help="Path to training artifact .pt file",
    )
    args = parser.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device     : {device}")
    print(f"Checkpoint : {args.checkpoint}")
    print(f"Artifact   : {args.dataset}")
    print()

    # Load artifact
    data = load_artifact(args.dataset)
    card_ids   = data["card_ids"]
    emb_matrix = data["embeddings"].numpy().astype(np.float32)
    card_meta  = load_card_meta_from_artifact(data)

    if not card_meta:
        print(
            "ERROR: artifact contains no card_meta — re-export with a current version of "
            "export_dataset.py",
            file=sys.stderr,
        )
        sys.exit(1)

    # Find query card
    query_id = find_card(args.card, card_meta)
    if query_id is None:
        print(f"ERROR: no card found matching '{args.card}'", file=sys.stderr)
        sys.exit(1)

    if query_id not in {cid: i for i, cid in enumerate(card_ids)}:
        print(f"ERROR: '{args.card}' found in card_meta but has no embedding", file=sys.stderr)
        sys.exit(1)

    qmeta = card_meta[query_id]
    print(f"Query      : {qmeta['name']}  [{qmeta.get('mana_cost', '—')}]")
    print(f"Type       : {qmeta.get('type_line', '—')}")
    print()

    # Load model
    input_dim = emb_matrix.shape[1]
    model = CardEncoder(input_dim=input_dim).to(device)
    load_checkpoint(model, args.checkpoint, device)

    # Project
    print(f"Projecting {len(card_ids)} cards…")
    proj = project_all(emb_matrix, model, device)   # (N, D)

    id_to_idx = {cid: i for i, cid in enumerate(card_ids)}
    q_vec  = proj[id_to_idx[query_id]]              # (D,) already L2-normalised
    scores = proj @ q_vec                            # (N,) cosine similarities

    # Rank, excluding query card
    ranked = [
        (float(scores[i]), card_ids[i], card_meta.get(card_ids[i], {}))
        for i in np.argsort(scores)[::-1]
        if card_ids[i] != query_id
    ]

    # Print table
    print(f"{'-' * 76}")
    print(f"  Top {args.top} nearest neighbours for: {qmeta['name']}")
    print(f"{'-' * 76}")
    print(f"  {'#':>3}  {'Score':>6}  {'Name':<32}  Type")
    print(f"  {'-'*3}  {'-'*6}  {'-'*32}  {'-'*28}")
    for rank, (score, card_id, meta) in enumerate(ranked[: args.top], 1):
        name  = (meta.get("name") or card_id)[:31]
        ttype = (meta.get("type_line") or "")[:28]
        print(f"  {rank:>3}  {score:>6.3f}  {name:<32}  {ttype}")
    print()


if __name__ == "__main__":
    main()
