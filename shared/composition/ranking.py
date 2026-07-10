"""Model-based pool ranking — Phase 1/2 checkpoints score cards in-slot.

Plan W4: the learned models never decide *how many* of anything (that is
the profile's job); they decide *which* candidates fill each quota.  A pool
arrives heuristic-ranked and leaves model-ranked; cards without embeddings
keep their heuristic order at the tail, so a missing embedding can never
crash a build.

Architecture classes mirror services/api/ops/model.py (which in turn
mirrors services/trainer/train.py).  Third copy — consolidate all three
here once the API is migrated onto the composition engine (plan W5).

torch is imported lazily so the rest of shared/composition stays usable
in torch-less environments (tests, API workers that only need quotas).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_CHECKPOINT_DIR = os.environ.get("MODEL_CHECKPOINT_DIR", "/checkpoints")

#: Relation used to score commander→card fit (see BilinearSynergyHead).
COMMANDER_FIT_RELATION = "decomposed_candidates"


class Ranker:
    """Frozen encoder + bilinear head scoring cards against one commander."""

    def __init__(self, encoder, bilinear, torch_mod):
        self._encoder = encoder
        self._bilinear = bilinear
        self._torch = torch_mod

    def score(
        self,
        commander_embedding: list[float],
        card_embeddings: list[list[float]],
        relation: str = COMMANDER_FIT_RELATION,
    ) -> list[float]:
        """Bilinear scores of each card against the commander."""
        torch = self._torch
        with torch.no_grad():
            z_cmd = self._encoder(torch.tensor([commander_embedding], dtype=torch.float32))
            z_cards = self._encoder(torch.tensor(card_embeddings, dtype=torch.float32))
            scores = self._bilinear.score(z_cmd.expand_as(z_cards), z_cards, relation)
        return scores.tolist()

    def rank_pool(
        self,
        pool: list[dict],
        commander_embedding: list[float],
        embeddings_by_id: dict[str, list[float]],
        relation: str = COMMANDER_FIT_RELATION,
    ) -> list[dict]:
        """Reorder a pool by model score, best first.

        Cards without an embedding keep their incoming (heuristic) order
        after all scored cards.
        """
        scored = [c for c in pool if c["id"] in embeddings_by_id]
        unscored = [c for c in pool if c["id"] not in embeddings_by_id]
        if not scored:
            return pool
        scores = self.score(
            commander_embedding, [embeddings_by_id[c["id"]] for c in scored], relation
        )
        order = sorted(zip(scored, scores), key=lambda p: -p[1])
        return [c for c, _ in order] + unscored


def load_ranker(checkpoint_dir: str | Path = DEFAULT_CHECKPOINT_DIR) -> Ranker | None:
    """Load the frozen encoder + bilinear head; None if unavailable.

    Encoder comes from phase1_best.pt — the canonical encoder since the
    bilinear Phase 2 (it freezes Phase 1's weights and never writes a new
    encoder).  phase2_best.pt is accepted as a fallback for volumes that
    only carry the legacy name.  (Order flipped in #151: the old
    phase2-first convention served a stale encoder after the #138 retrain.)
    Returns None — never raises — when torch or the checkpoints are
    missing, so callers can fall back to heuristic ranking.
    """
    try:
        import torch
        import torch.nn as nn
        import torch.nn.functional as F
    except ImportError:
        log.warning("torch unavailable — model ranking disabled")
        return None

    class CardEncoder(nn.Module):  # mirrors ops/model.py
        def __init__(self, input_dim=768, hidden_dim=512, output_dim=256):
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(input_dim, hidden_dim),
                nn.GELU(),
                nn.LayerNorm(hidden_dim),
                nn.Dropout(0.1),
                nn.Linear(hidden_dim, output_dim),
            )

        def forward(self, x):
            return F.normalize(self.net(x), dim=-1)

    class BilinearSynergyHead(nn.Module):  # mirrors ops/model.py
        RELATIONS = ["effect_peer", "ability_trigger", "combo", "decomposed_candidates"]

        def __init__(self, embed_dim=256):
            super().__init__()
            self.rel_to_idx = {r: i for i, r in enumerate(self.RELATIONS)}
            self.W = nn.ParameterList(
                [nn.Parameter(torch.eye(embed_dim)) for _ in self.RELATIONS]
            )

        def score(self, z_a, z_b, relation):
            W = self.W[self.rel_to_idx[relation] if isinstance(relation, str) else relation]
            return (z_a @ W * z_b).sum(dim=-1)

    ckpt = Path(checkpoint_dir)
    enc_path = next((p for p in (ckpt / "phase1_best.pt", ckpt / "phase2_best.pt") if p.exists()), None)
    bl_path = ckpt / "phase2_bilinear_best.pt"
    if enc_path is None or not bl_path.exists():
        log.warning("checkpoints missing in %s — model ranking disabled", ckpt)
        return None

    enc_state = torch.load(enc_path, map_location="cpu")
    input_dim = enc_state["net.0.weight"].shape[1]
    output_dim = enc_state["net.4.weight"].shape[0]
    encoder = CardEncoder(input_dim=input_dim, output_dim=output_dim)
    encoder.load_state_dict(enc_state)
    encoder.eval()

    bl_state = torch.load(bl_path, map_location="cpu")
    bilinear = BilinearSynergyHead(embed_dim=output_dim)
    bilinear.load_state_dict(bl_state)
    bilinear.eval()

    log.info("model ranking: encoder %s (in=%d out=%d) + %s", enc_path.name, input_dim, output_dim, bl_path.name)
    return Ranker(encoder, bilinear, torch)
