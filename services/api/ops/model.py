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
