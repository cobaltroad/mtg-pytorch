"""Centroid expansion evaluator for a commander.

Projects all card embeddings through the Phase 2 encoder, computes the centroid
of the archetype-matched subset of the commander's positive set, then displays
the top-K nearest-neighbour expansion candidates that are colour-legal and not
already in the positive set.

Only cards whose trigger_event matches one of the commander's archetype labels
contribute to the centroid.  This prevents the mean from collapsing to a generic
colour-identity cluster when the positive set mixes unrelated trigger_event types.

Requires card_trigger_events to be present in the artifact
(re-run export_dataset_commanders if missing).

Usage
-----
    python eval_commander.py "Tyvar the Bellicose"
    python eval_commander.py "Anje Falkenrath" --top 30 --checkpoint phase2_best
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from train import CardEncoder, load_artifact, load_checkpoint, CHECKPOINT_DIR


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
            batch = torch.from_numpy(emb_matrix[i : i + batch_size]).to(device)
            projected.append(model(batch).cpu().numpy())
    return np.concatenate(projected, axis=0)


def find_commander(
    query: str,
    decks: list[dict],
    card_ids: list[str],
    card_meta: dict,
) -> dict | None:
    """Return the deck entry whose commander name best matches query."""
    q = query.lower()
    best = None
    for deck in decks:
        name = card_meta.get(card_ids[deck["commander_idx"]], {}).get("name", "")
        if name.lower() == q:
            return deck
        if best is None and q in name.lower():
            best = deck
    return best


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Show centroid expansion candidates for a commander (no DB required)"
    )
    parser.add_argument("commander", help="Commander name (partial match ok)")
    parser.add_argument("--top", type=int, default=20,
                        help="Candidates to display (default: 20)")
    parser.add_argument("--checkpoint", default="phase2_best",
                        help="Checkpoint name in CHECKPOINT_DIR (default: phase2_best)")
    parser.add_argument(
        "--dataset",
        default=str(Path(__file__).parent.parent.parent / "ingest_cache" / "mtg_commanders.pt"),
        help="Path to mtg_commanders.pt",
    )
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        print(f"ERROR: artifact not found: {dataset_path}", file=sys.stderr)
        sys.exit(1)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device     : {device}")
    print(f"Checkpoint : {args.checkpoint}")
    print(f"Artifact   : {dataset_path}")
    print()

    data      = load_artifact(str(dataset_path))
    card_ids  = data["card_ids"]
    emb_np    = data["embeddings"].numpy().astype(np.float32)
    card_meta = data.get("card_meta", {})
    color_ids = data.get("color_identities", {})
    decks     = data["decks"]

    if not card_meta:
        print("ERROR: artifact contains no card_meta — re-export with export_dataset_commanders.py",
              file=sys.stderr)
        sys.exit(1)

    deck = find_commander(args.commander, decks, card_ids, card_meta)
    if deck is None:
        print(f"ERROR: no commander found matching '{args.commander}'", file=sys.stderr)
        sys.exit(1)

    cmd_idx  = deck["commander_idx"]
    cmd_id   = card_ids[cmd_idx]
    cmd_meta = card_meta.get(cmd_id, {})
    archetype = deck.get("archetype", "")

    print("=" * 70)
    print(f"  {cmd_meta.get('name', cmd_id)}")
    print("=" * 70)
    print(f"  Color identity : {deck['color_identity']}")
    print(f"  Archetype      : {archetype}")
    print(f"  Positive set   : {len(deck['card_idxs'])} cards")

    pos_idxs = deck["card_idxs"]
    card_te  = deck.get("card_trigger_events", [])
    archetype_labels = [s.strip() for s in archetype.split(",") if s.strip()]

    if card_te:
        label_counts = {
            label: sum(1 for te in card_te if te == label)
            for label in archetype_labels
        }
        basis_desc = "  ".join(f"{lb}({n})" for lb, n in label_counts.items() if n)
        print(f"  Centroid basis : one per archetype label — {basis_desc}")
    else:
        print("  Centroid basis : all positives (no card_trigger_events — re-export artifact)")
    print()

    # Load model + project
    model = CardEncoder(input_dim=emb_np.shape[1]).to(device)
    load_checkpoint(model, args.checkpoint, device)

    print(f"Projecting {len(card_ids)} cards…")
    proj   = project_all(emb_np, model, device)  # (N, D) L2-normalised
    proj_t = torch.from_numpy(proj)

    excluded = set(pos_idxs) | {cmd_idx}
    cmd_ci   = frozenset(deck["color_identity"])
    card_ci  = [frozenset(color_ids.get(cid, [])) for cid in card_ids]

    # One centroid per archetype label; union of top-K, best score wins per card
    centroids: list[torch.Tensor] = []
    for label in archetype_labels:
        idxs = [idx for idx, te in zip(pos_idxs, card_te) if te == label] if card_te else []
        if idxs:
            c = proj_t[idxs].mean(dim=0)
            centroids.append(F.normalize(c.unsqueeze(0), dim=1).squeeze(0))
    if not centroids:
        c = proj_t[pos_idxs].mean(dim=0)
        centroids.append(F.normalize(c.unsqueeze(0), dim=1).squeeze(0))

    best_score: dict[int, float] = {}
    for centroid in centroids:
        sims = (proj_t @ centroid).numpy()
        for i in range(len(card_ids)):
            if i in excluded or card_ci[i] > cmd_ci:
                continue
            s = float(sims[i])
            if s > best_score.get(i, -1.0):
                best_score[i] = s

    candidates = sorted(best_score.items(), key=lambda x: -x[1])

    top = args.top
    print(f"{'─' * 76}")
    print(f"  Top {top} centroid expansion candidates for: {cmd_meta.get('name', cmd_id)}")
    print(f"{'─' * 76}")
    print(f"  {'#':>3}  {'Score':>6}  {'Name':<32}  Type")
    print(f"  {'─'*3}  {'─'*6}  {'─'*32}  {'─'*28}")
    for rank, (idx, score) in enumerate(candidates[:top], 1):
        cid   = card_ids[idx]
        meta  = card_meta.get(cid, {})
        name  = (meta.get("name") or cid)[:31]
        ttype = (meta.get("type_line") or "")[:28]
        print(f"  {rank:>3}  {score:>6.3f}  {name:<32}  {ttype}")
    print()

    stored = deck.get("centroid_expansion_idxs")
    if stored is not None:
        ck = data.get("meta", {}).get("centroid_expansion_checkpoint", "?")
        print(f"  [artifact has {len(stored)} stored expansion candidates — checkpoint: {ck}]")
    else:
        print("  [run: .\\scripts\\run.ps1 -Train 3 to store expansion candidates in the artifact]")
    print()


if __name__ == "__main__":
    main()
