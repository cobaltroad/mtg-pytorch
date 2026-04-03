"""Model architecture definitions for inference.

These are the CardEncoder and CommanderScorer nn.Module classes copied from
services/trainer/train.py — no training code, no data loading, just the
architecture needed to load a checkpoint and run inference.
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


class CardEncoder(nn.Module):
    """Projects a pre-computed embedding into a shared latent space."""

    def __init__(self, input_dim: int = 768, hidden_dim: int = 512, output_dim: int = 256):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.GELU(),
            nn.LayerNorm(hidden_dim),
            nn.Dropout(0.1),
            nn.Linear(hidden_dim, output_dim),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return F.normalize(self.net(x), dim=-1)


class CommanderScorer(nn.Module):
    """Scores a card relative to a commander.

    Takes the frozen Phase 2 encoder projections of both cards and returns
    a scalar compatibility score.  The encoder is never updated by this head.
    """

    def __init__(self, embed_dim: int = 256):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(embed_dim * 2, embed_dim),
            nn.ReLU(),
            nn.Linear(embed_dim, 1),
        )

    def forward(self, z_cmd: torch.Tensor, z_card: torch.Tensor) -> torch.Tensor:
        """
        z_cmd  : (D,) or (N, D) — commander projection.
        z_card : (N, D) — candidate card projections.
        Returns: (N,) scores.
        """
        if z_cmd.dim() == 1:
            z_cmd = z_cmd.unsqueeze(0).expand_as(z_card)
        return self.net(torch.cat([z_cmd, z_card], dim=-1)).squeeze(-1)


class BilinearSynergyHead(nn.Module):
    """Phase 2 (Option B) inference head: relation-specific bilinear scoring.

    score(A, B, r) = A^T W_r B

    At inference the ``decomposed_candidates`` relation is used to score how
    well a candidate card fits a given commander's strategy.  W matrices are
    loaded from the ``phase2_bilinear_best.pt`` checkpoint.
    """

    RELATIONS: list[str] = [
        "effect_peer",
        "ability_trigger",
        "combo",
        "decomposed_candidates",
    ]

    def __init__(self, embed_dim: int = 256):
        super().__init__()
        self.embed_dim = embed_dim
        self.rel_to_idx: dict[str, int] = {r: i for i, r in enumerate(self.RELATIONS)}
        self.W = nn.ParameterList(
            [nn.Parameter(torch.eye(embed_dim)) for _ in self.RELATIONS]
        )

    def _rel_idx(self, relation: int | str) -> int:
        if isinstance(relation, str):
            return self.rel_to_idx[relation]
        return relation

    def score(
        self, z_a: torch.Tensor, z_b: torch.Tensor, relation: int | str
    ) -> torch.Tensor:
        """Return (B,) pairwise bilinear scores.

        score[i] = z_a[i]^T W_r z_b[i]

        z_a, z_b: (B, D) L2-normalised encoder outputs.
        """
        W = self.W[self._rel_idx(relation)]
        return (z_a @ W * z_b).sum(dim=-1)  # (B,)
