"""Centroid expansion for the commander artifact.

For each commander, projects all card embeddings through the Phase 2 encoder,
then computes the mean of the projected embeddings for cards whose trigger_event
matches one of the commander's archetype labels.  Using only archetype-matched
cards prevents the centroid from collapsing to a generic "colour-identity blob"
when the positive set contains unrelated trigger_event categories.

Top-K colour-legal nearest neighbours of the centroid (excluding the positive
set and commander) are stored as centroid_expansion_idxs per deck entry.

Idempotent — re-running with the same arguments overwrites prior results.

Requires: mtg_commanders.pt built with a version of export_dataset_commanders.py
that stores card_trigger_events alongside card_idxs.

Usage
-----
    python centroid_expansion.py
    python centroid_expansion.py --checkpoint phase2_best --top-k 50
    python centroid_expansion.py --dataset .\\ingest_cache\\mtg_commanders.pt
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from train import CardEncoder, load_artifact, load_checkpoint, CHECKPOINT_DIR

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DEFAULT_DATASET = str(
    Path(__file__).parent.parent.parent / "ingest_cache" / "mtg_commanders.pt"
)
DEFAULT_TOP_K = 50
DEFAULT_CAP   = 100


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


def compute_centroid_expansions(
    decks: list[dict],
    proj: np.ndarray,
    card_ids: list[str],
    color_identities: dict[str, list[str]],
    top_k: int,
    cap: int,
) -> int:
    """Annotate each deck dict in-place with centroid_expansion_idxs.

    Groups positive-set cards by their card_trigger_events value and computes
    one centroid per distinct trigger_event.  Candidates are the union of top-K
    from each centroid, keeping the best score per card across all centroids.

    Using trigger_event groups (from synergy_edges) rather than archetype labels
    (from decompose_commanders) avoids vocabulary mismatch between the two
    systems, and ensures the centroid vocabulary matches what Phase 2 was
    trained on.

    Falls back to a single centroid over all positives when card_trigger_events
    is absent (old artifact) or all trigger_events are empty.

    Returns the number of commanders that received ≥1 expansion candidate.
    """
    from collections import defaultdict

    card_ci = [frozenset(color_identities.get(cid, [])) for cid in card_ids]
    proj_t  = torch.from_numpy(proj)   # (N, D) float32, L2-normalised
    k_store = min(top_k, cap)

    covered = 0
    for deck in decks:
        cmd_idx  = deck["commander_idx"]
        pos_idxs = deck["card_idxs"]
        cmd_ci   = frozenset(deck["color_identity"])

        if not pos_idxs:
            deck["centroid_expansion_idxs"] = []
            continue

        card_te = deck.get("card_trigger_events", [])
        excluded = set(pos_idxs) | {cmd_idx}

        # Group by trigger_event; skip empty-string entries (commander_value payoffs)
        te_groups: dict[str, list[int]] = defaultdict(list)
        for idx, te in zip(pos_idxs, card_te):
            if te:
                te_groups[te].append(idx)

        centroids: list[torch.Tensor] = []
        for idxs in te_groups.values():
            c = F.normalize(proj_t[idxs].mean(dim=0).unsqueeze(0), dim=1).squeeze(0)
            centroids.append(c)
        if not centroids:
            # Fallback: single centroid over all positives
            c = F.normalize(proj_t[pos_idxs].mean(dim=0).unsqueeze(0), dim=1).squeeze(0)
            centroids.append(c)

        # Union of top-K per centroid; keep best score per card across centroids
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
        deck["centroid_expansion_idxs"] = [idx for idx, _ in candidates[:k_store]]

        if candidates:
            covered += 1

    return covered


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Add centroid expansion candidates to the commander artifact"
    )
    parser.add_argument(
        "--checkpoint", default="phase2_best",
        help="Checkpoint name in CHECKPOINT_DIR (default: phase2_best)",
    )
    parser.add_argument(
        "--dataset", default=DEFAULT_DATASET,
        help="Path to mtg_commanders.pt (default: ingest_cache/mtg_commanders.pt)",
    )
    parser.add_argument(
        "--top-k", type=int, default=DEFAULT_TOP_K,
        help=f"Expansion candidates to store per commander (default: {DEFAULT_TOP_K})",
    )
    parser.add_argument(
        "--cap", type=int, default=DEFAULT_CAP,
        help=f"Hard cap on stored candidates per commander (default: {DEFAULT_CAP})",
    )
    args = parser.parse_args()

    dataset_path    = Path(args.dataset)
    checkpoint_path = CHECKPOINT_DIR / f"{args.checkpoint}.pt"

    if not dataset_path.exists():
        log.error("Artifact not found: %s", dataset_path)
        sys.exit(1)
    if not checkpoint_path.exists():
        log.error("Checkpoint not found: %s", checkpoint_path)
        sys.exit(1)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    log.info("Device     : %s", device)
    log.info("Checkpoint : %s  (%s)", args.checkpoint, checkpoint_path)
    log.info("Artifact   : %s", dataset_path)
    log.info("Top-K      : %d  (cap: %d)", args.top_k, args.cap)

    data      = load_artifact(str(dataset_path))
    card_ids  = data["card_ids"]
    emb_np    = data["embeddings"].numpy().astype(np.float32)
    decks     = data["decks"]
    color_ids = data.get("color_identities", {})

    log.info("Loaded: %d cards, %d commander decks", len(card_ids), len(decks))

    has_te = sum(1 for d in decks if d.get("card_trigger_events"))
    if has_te == 0:
        log.warning(
            "No card_trigger_events found in artifact — re-run "
            "export_dataset_commanders to get archetype-filtered centroids"
        )

    model = CardEncoder(input_dim=emb_np.shape[1]).to(device)
    load_checkpoint(model, args.checkpoint, device)

    log.info("Projecting %d card embeddings…", len(card_ids))
    proj = project_all(emb_np, model, device)

    log.info("Computing centroid expansions for %d commanders…", len(decks))
    covered = compute_centroid_expansions(
        decks, proj, card_ids, color_ids, args.top_k, args.cap,
    )
    log.info(
        "Centroid expansion complete: %d/%d commanders have ≥1 candidate",
        covered, len(decks),
    )

    data["meta"]["centroid_expansion_top_k"]     = min(args.top_k, args.cap)
    data["meta"]["centroid_expansion_checkpoint"] = args.checkpoint

    log.info("Saving augmented artifact → %s", dataset_path)
    torch.save(data, dataset_path)
    size_mb = dataset_path.stat().st_size / 1e6
    log.info("Done. %.1f MB", size_mb)


if __name__ == "__main__":
    main()
