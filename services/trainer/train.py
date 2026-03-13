"""
MTG Commander deck construction trainer.

Training progression
--------------------
Phase 1 – Text equivalence
    Contrastive loss on card embeddings: same-named reprints → positive pairs,
    random cards → negative pairs.

Phase 2 – Ability-trigger synergy
    Binary classification: given (card_a, card_b), predict synergy_edges score.
    Positives from synergy_edges (ability_trigger); negatives sampled randomly.

Phase 3 – Deck co-occurrence (human signal)
    Multi-label: given a commander embedding, rank which cards appear in
    human-constructed decks.  Trained on the `decks` table.

Phase 4 – Generative deck construction
    Autoregressive: predict the next card given commander + partial deck.
    The model is NOT constrained to reproduce existing decklists — it learns
    the distribution then samples freely at inference time.
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

CHECKPOINT_DIR = Path(os.environ.get("CHECKPOINT_DIR", "/checkpoints"))
DATABASE_URL = os.environ.get("DATABASE_URL", "")

try:
    import wandb
    WANDB_ENABLED = bool(os.environ.get("WANDB_API_KEY"))
except ImportError:
    WANDB_ENABLED = False


# ── Model ─────────────────────────────────────────────────────────────────────

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
        # Final projection: score each candidate card
        self.scorer = nn.Linear(embed_dim, 1)

    def forward(
        self,
        commander_emb: torch.Tensor,    # (B, D)
        deck_embs: torch.Tensor,         # (B, T, D)  partial deck so far
        candidate_embs: torch.Tensor,    # (B, C, D)  cards to score
    ) -> torch.Tensor:
        # commander as memory (cross-attention source)
        memory = commander_emb.unsqueeze(1)  # (B, 1, D)

        # encode deck context
        deck_ctx = self.decoder(deck_embs, memory)          # (B, T, D)
        ctx = deck_ctx.mean(dim=1, keepdim=True)            # (B, 1, D)

        # score candidates
        scores = (ctx * candidate_embs).sum(dim=-1)         # (B, C)
        return scores


# ── Datasets ──────────────────────────────────────────────────────────────────

class SynergyDataset(Dataset):
    """Phase 2: binary synergy pairs loaded from DB."""

    def __init__(self, pairs: list[tuple], embeddings: dict):
        self.pairs = pairs
        self.embeddings = embeddings

    def __len__(self):
        return len(self.pairs)

    def __getitem__(self, idx):
        card_a, card_b, label = self.pairs[idx]
        emb_a = torch.tensor(self.embeddings[card_a], dtype=torch.float32)
        emb_b = torch.tensor(self.embeddings[card_b], dtype=torch.float32)
        return emb_a, emb_b, torch.tensor(label, dtype=torch.float32)


class DeckDataset(Dataset):
    """Phase 3/4: (commander, deck_cards) pairs from human decklists."""

    def __init__(self, decks: list[dict], embeddings: dict):
        self.decks = decks
        self.embeddings = embeddings

    def __len__(self):
        return len(self.decks)

    def __getitem__(self, idx):
        deck = self.decks[idx]
        cmd_emb = torch.tensor(self.embeddings[deck["commander_id"]], dtype=torch.float32)
        card_embs = torch.stack([
            torch.tensor(self.embeddings[c], dtype=torch.float32)
            for c in deck["card_ids"]
            if c in self.embeddings
        ])
        return cmd_emb, card_embs


# ── Training loops ────────────────────────────────────────────────────────────

def train_synergy_phase(model: CardEncoder, dataset: SynergyDataset, epochs: int, lr: float):
    """Phase 2: contrastive / binary cross-entropy on ability-trigger pairs."""
    loader = DataLoader(dataset, batch_size=256, shuffle=True, num_workers=2)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    device = next(model.parameters()).device

    for epoch in range(epochs):
        total_loss = 0.0
        for emb_a, emb_b, labels in loader:
            emb_a, emb_b, labels = emb_a.to(device), emb_b.to(device), labels.to(device)
            proj_a = model(emb_a)
            proj_b = model(emb_b)
            similarity = (proj_a * proj_b).sum(dim=-1)
            loss = F.binary_cross_entropy_with_logits(similarity, labels)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            total_loss += loss.item()

        avg = total_loss / len(loader)
        log.info("Synergy epoch %d/%d  loss=%.4f", epoch + 1, epochs, avg)
        if WANDB_ENABLED:
            wandb.log({"phase": 2, "epoch": epoch, "synergy_loss": avg})


def save_checkpoint(model: nn.Module, name: str):
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    path = CHECKPOINT_DIR / f"{name}.pt"
    torch.save(model.state_dict(), path)
    # Also write 'latest' symlink
    latest = CHECKPOINT_DIR / "latest.pt"
    if latest.is_symlink():
        latest.unlink()
    latest.symlink_to(path.name)
    log.info("Checkpoint saved: %s", path)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", type=int, choices=[1, 2, 3, 4], default=2)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--lr", type=float, default=1e-4)
    args = parser.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    log.info("Training on %s", device)

    if WANDB_ENABLED:
        wandb.init(project=os.environ.get("WANDB_PROJECT", "edh-builder"))

    # TODO: load embeddings and pairs/decks from DB
    # Placeholder — replace with real DB loading once ingest has run
    log.warning("No data loaded — training loop is a stub until ingest runs.")

    model = CardEncoder().to(device)
    save_checkpoint(model, "phase1_init")
    log.info("Done.")


if __name__ == "__main__":
    main()
