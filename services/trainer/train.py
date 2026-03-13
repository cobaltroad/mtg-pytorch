"""
MTG Commander deck construction trainer.

Training progression
--------------------
Phase 1 – Text equivalence
    Contrastive loss on card embeddings: same-named reprints → positive pairs,
    random cards → negative pairs.

Phase 2 – Ability-trigger synergy
    Binary classification: given (card_a, card_b), predict synergy_edges score.
    Positives from synergy_edges (ability_trigger); negatives sampled randomly
    at a configurable ratio (default 3:1 neg:pos).

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
import random
from pathlib import Path

import numpy as np
import psycopg2
import psycopg2.extras
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

CHECKPOINT_DIR = Path(os.environ.get("CHECKPOINT_DIR", "/checkpoints"))
DATABASE_URL = os.environ.get("DATABASE_URL", "")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

try:
    import wandb
    WANDB_ENABLED = bool(os.environ.get("WANDB_API_KEY"))
except ImportError:
    WANDB_ENABLED = False


# ── DB helpers ────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(DATABASE_URL)


def load_embeddings(model_name: str = EMBEDDING_MODEL) -> dict[str, np.ndarray]:
    """Return {card_id (str): np.ndarray} for all embedded cards."""
    log.info("Loading embeddings (model=%s)…", model_name)
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("""
                SELECT card_id::text, embedding
                FROM card_embeddings
                WHERE model = %s
            """, (model_name,))
            rows = cur.fetchall()

    embeddings = {}
    for row in rows:
        vec = row["embedding"]
        # psycopg2 returns pgvector as a string '[v1,v2,...]'
        if isinstance(vec, str):
            vec = np.fromstring(vec.strip("[]"), sep=",", dtype=np.float32)
        else:
            vec = np.array(vec, dtype=np.float32)
        embeddings[row["card_id"]] = vec

    log.info("Loaded %d embeddings", len(embeddings))
    return embeddings


def load_synergy_pairs(
    embeddings: dict,
    neg_ratio: int = 3,
) -> list[tuple[str, str, float]]:
    """Return [(card_a_id, card_b_id, label)] with balanced pos/neg pairs.

    Positives: synergy_edges rows with score_type='ability_trigger'.
    Negatives: random pairs sampled from cards that have embeddings,
               at neg_ratio × len(positives).
    """
    log.info("Loading synergy pairs…")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT card_a::text, card_b::text
                FROM synergy_edges
                WHERE score_type = 'ability_trigger'
            """)
            positives = [
                (r[0], r[1], 1.0) for r in cur.fetchall()
                if r[0] in embeddings and r[1] in embeddings
            ]

    log.info("  %d positive pairs", len(positives))

    all_ids = list(embeddings.keys())
    pos_set = {(a, b) for a, b, _ in positives}
    n_neg = len(positives) * neg_ratio
    negatives = []
    attempts = 0
    while len(negatives) < n_neg and attempts < n_neg * 10:
        a, b = random.sample(all_ids, 2)
        if (a, b) not in pos_set and (b, a) not in pos_set:
            negatives.append((a, b, 0.0))
        attempts += 1

    log.info("  %d negative pairs", len(negatives))
    pairs = positives + negatives
    random.shuffle(pairs)
    return pairs


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


# ── Datasets ──────────────────────────────────────────────────────────────────

class SynergyDataset(Dataset):
    """Phase 2: binary synergy pairs."""

    def __init__(self, pairs: list[tuple], embeddings: dict):
        self.pairs = pairs
        self.embeddings = embeddings

    def __len__(self):
        return len(self.pairs)

    def __getitem__(self, idx):
        card_a, card_b, label = self.pairs[idx]
        emb_a = torch.from_numpy(self.embeddings[card_a])
        emb_b = torch.from_numpy(self.embeddings[card_b])
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
        cmd_emb = torch.from_numpy(self.embeddings[deck["commander_id"]])
        card_embs = torch.stack([
            torch.from_numpy(self.embeddings[c])
            for c in deck["card_ids"]
            if c in self.embeddings
        ])
        return cmd_emb, card_embs


# ── Training loops ────────────────────────────────────────────────────────────

def train_synergy_phase(
    model: CardEncoder,
    dataset: SynergyDataset,
    epochs: int,
    lr: float,
    batch_size: int = 256,
):
    """Phase 2: binary cross-entropy on ability-trigger synergy pairs."""
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=True, num_workers=0)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    device = next(model.parameters()).device

    best_loss = float("inf")
    for epoch in range(epochs):
        model.train()
        total_loss = 0.0
        for emb_a, emb_b, labels in loader:
            emb_a  = emb_a.to(device)
            emb_b  = emb_b.to(device)
            labels = labels.to(device)

            proj_a = model(emb_a)
            proj_b = model(emb_b)
            similarity = (proj_a * proj_b).sum(dim=-1)
            loss = F.binary_cross_entropy_with_logits(similarity, labels)

            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total_loss += loss.item()

        scheduler.step()
        avg = total_loss / len(loader)
        log.info("Phase 2  epoch %d/%d  loss=%.4f  lr=%.2e",
                 epoch + 1, epochs, avg, scheduler.get_last_lr()[0])

        if WANDB_ENABLED:
            wandb.log({"phase": 2, "epoch": epoch + 1, "loss": avg,
                       "lr": scheduler.get_last_lr()[0]})

        if avg < best_loss:
            best_loss = avg
            save_checkpoint(model, "phase2_best")

    save_checkpoint(model, f"phase2_epoch{epochs}")


# ── Checkpoint helpers ────────────────────────────────────────────────────────

def save_checkpoint(model: nn.Module, name: str):
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    path = CHECKPOINT_DIR / f"{name}.pt"
    torch.save(model.state_dict(), path)
    latest = CHECKPOINT_DIR / "latest.pt"
    if latest.is_symlink():
        latest.unlink()
    latest.symlink_to(path.name)
    log.info("Checkpoint saved: %s", path)


def load_checkpoint(model: nn.Module, name: str, device: torch.device) -> nn.Module:
    path = CHECKPOINT_DIR / f"{name}.pt"
    if path.exists():
        model.load_state_dict(torch.load(path, map_location=device))
        log.info("Loaded checkpoint: %s", path)
    else:
        log.warning("Checkpoint not found: %s — starting from scratch", path)
    return model


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", type=int, choices=[1, 2, 3, 4], default=2)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--neg-ratio", type=int, default=3,
                        help="Negative pairs per positive for phase 2")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from latest checkpoint")
    args = parser.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    log.info("Training phase %d on %s", args.phase, device)

    if WANDB_ENABLED:
        wandb.init(
            project=os.environ.get("WANDB_PROJECT", "edh-builder"),
            config=vars(args),
        )

    if args.phase == 2:
        embeddings = load_embeddings()
        if not embeddings:
            log.error("No embeddings found — run the ingest pipeline first.")
            return

        pairs = load_synergy_pairs(embeddings, neg_ratio=args.neg_ratio)
        if not pairs:
            log.error("No synergy pairs found — run compute_synergy stage first.")
            return

        dataset = SynergyDataset(pairs, embeddings)
        log.info("Dataset: %d pairs", len(dataset))

        model = CardEncoder().to(device)
        if args.resume:
            load_checkpoint(model, "phase2_best", device)

        train_synergy_phase(model, dataset, args.epochs, args.lr, args.batch_size)

    else:
        log.warning("Phase %d not yet implemented.", args.phase)

    if WANDB_ENABLED:
        wandb.finish()


if __name__ == "__main__":
    main()
