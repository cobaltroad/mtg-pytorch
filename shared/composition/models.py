"""Canonical model architectures — the single definition (issue #152).

Historically these classes were copied in three places (trainer, API ops,
composition ranking) with a "keep in sync" comment.  The API copy died with
Phase 3/4 retirement (#151); this module replaces the remaining two.

Importers:
  services/trainer/train.py            — training (GPU machine)
  shared/composition/ranking.py        — inference-side in-slot ranking

This module imports torch at module level — import it lazily (inside a
function) from any code path that must stay torch-optional, as ranking.py
does.

Checkpoint compatibility: state-dict keys must remain exactly as they are
(``net.0.weight`` …, ``W.0`` …) — every existing phase1/phase2 checkpoint
loads into these definitions unchanged.
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


class CardEncoder(nn.Module):
    """Projects a pre-computed text embedding into the shared latent space.

    Phase 1 trains it with NT-Xent; every later consumer freezes it.
    """

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


class BilinearSynergyHead(nn.Module):
    """Phase 2 (Option B): relation-specific bilinear scoring matrices W_r.

    score(A, B, r) = A^T W_r B

    Trained with asymmetric InfoNCE per relation over a frozen Phase 1
    encoder.  At inference the ``decomposed_candidates`` relation scores
    commander→card fit for the composition builder's in-slot ranking.
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
        # Identity init: score(A, B, r) = A · B (cosine sim for unit vectors),
        # so Phase 1 geometry is preserved exactly at the start of training.
        self.W = nn.ParameterList(
            [nn.Parameter(torch.eye(embed_dim)) for _ in self.RELATIONS]
        )

    def _rel_idx(self, relation: int | str) -> int:
        if isinstance(relation, str):
            return self.rel_to_idx[relation]
        return relation

    def score_matrix(
        self, z_a: torch.Tensor, z_b: torch.Tensor, relation: int | str
    ) -> torch.Tensor:
        """(B_a, B_b) bilinear score matrix: [i, j] = z_a[i]^T W_r z_b[j].

        Diagonal entries are positive-pair scores; off-diagonals are the
        in-batch negatives for InfoNCE training.
        """
        W = self.W[self._rel_idx(relation)]
        return z_a @ W @ z_b.T

    def score(
        self, z_a: torch.Tensor, z_b: torch.Tensor, relation: int | str
    ) -> torch.Tensor:
        """Pairwise bilinear scores: score[i] = z_a[i]^T W_r z_b[i]."""
        W = self.W[self._rel_idx(relation)]
        return (z_a @ W * z_b).sum(dim=-1)
