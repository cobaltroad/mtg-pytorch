"""Model architecture definitions for inference.

These are the CardEncoder and DeckConstructor nn.Module classes copied from
services/trainer/train.py — no training code, no data loading, just the
architecture needed to load a checkpoint and run inference.
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


class CardEncoder(nn.Module):
    """Projects a pre-computed embedding into a shared latent space."""

    def __init__(self, input_dim: int = 384, hidden_dim: int = 512, output_dim: int = 256):
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


class DeckConstructor(nn.Module):
    """
    Given a commander embedding + partial deck state, scores candidate cards.

    Architecture: transformer decoder over the partial deck sequence,
    attending to the commander as a prefix token.
    """

    def __init__(self, embed_dim: int = 256, n_heads: int = 4, n_layers: int = 3):
        super().__init__()
        self.card_encoder = CardEncoder(output_dim=embed_dim)
        decoder_layer = nn.TransformerDecoderLayer(
            d_model=embed_dim, nhead=n_heads, dim_feedforward=embed_dim * 4,
            dropout=0.1, batch_first=True,
        )
        self.decoder = nn.TransformerDecoder(decoder_layer, num_layers=n_layers)
        self.scorer = nn.Linear(embed_dim, 1)

    def forward(
        self,
        commander_emb: torch.Tensor,    # (B, D)
        deck_embs: torch.Tensor,         # (B, T, D)  partial deck so far
        candidate_embs: torch.Tensor,    # (B, C, D)  cards to score
    ) -> torch.Tensor:
        memory = commander_emb.unsqueeze(1)             # (B, 1, D)
        deck_ctx = self.decoder(deck_embs, memory)      # (B, T, D)
        ctx = deck_ctx.mean(dim=1, keepdim=True)        # (B, 1, D)
        return (ctx * candidate_embs).sum(dim=-1)       # (B, C)
