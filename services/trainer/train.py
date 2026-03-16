"""
MTG Commander deck construction trainer.

Training progression
--------------------
Phase 1 – Text equivalence (SimCLR-style)
    NT-Xent contrastive loss: two Gaussian-noised views of the same card
    embedding are the positive pair; all other cards in the batch are
    in-batch negatives.  No reprints required — works with one row per
    oracle_id.  Teaches the encoder to produce stable, denoised representations
    that preserve sentence-transformer similarity structure.

Phase 2 – Ability-trigger synergy
    Binary classification: given (card_a, card_b), predict synergy_edges score.
    Positives from synergy_edges (ability_trigger); negatives are a mix of hard
    negatives (semantically similar cards that are NOT synergistic, mined from
    the embedding space) and random negatives (default 50/50 split, 3:1 total
    neg:pos ratio).  Label smoothing is applied to handle noisy regex-derived
    positive labels (default ε=0.1 → pos→0.95, neg→0.05).

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
import math
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


def _mine_hard_negatives(
    positives: list[tuple],
    embeddings: dict,
    all_ids: list[str],
    pos_set: set,
    n_hard: int,
    top_k: int = 200,
) -> list[tuple[str, str, float]]:
    """Return hard negatives: cards semantically similar to card_a but not synergistic.

    For each unique card_a in positives, ranks all other cards by cosine similarity
    and picks the highest-similarity card not already in pos_set.  This forces the
    model to learn the synergy distinction rather than just text similarity.
    """
    log.info("Mining %d hard negatives (top_k=%d)…", n_hard, top_k)
    id_to_idx = {card_id: i for i, card_id in enumerate(all_ids)}
    emb_matrix = np.stack([embeddings[k] for k in all_ids])          # (N, 384)
    norms = np.linalg.norm(emb_matrix, axis=1, keepdims=True)
    normed = (emb_matrix / np.maximum(norms, 1e-8)).astype(np.float32)  # (N, 384)

    unique_a = list({a for a, _, _ in positives})
    random.shuffle(unique_a)
    # How many hard negatives to collect per anchor card
    per_anchor = max(1, -(-n_hard // max(len(unique_a), 1)))  # ceiling division

    hard_negs: list[tuple[str, str, float]] = []
    for card_a in unique_a:
        if len(hard_negs) >= n_hard:
            break
        a_vec = normed[id_to_idx[card_a]]          # (384,)
        sims = normed @ a_vec                       # (N,) cosine similarities
        ranked = np.argsort(sims)[::-1]             # descending
        collected = 0
        for idx in ranked[1: top_k + 1]:           # skip self (idx 0)
            cand = all_ids[int(idx)]
            if (card_a, cand) not in pos_set and (cand, card_a) not in pos_set:
                hard_negs.append((card_a, cand, 0.0))
                collected += 1
                if collected >= per_anchor or len(hard_negs) >= n_hard:
                    break

    log.info("  %d hard negatives mined", len(hard_negs))
    return hard_negs


def load_synergy_pairs(
    embeddings: dict,
    neg_ratio: int = 3,
    sample: int = 500_000,
    hard_neg_frac: float = 0.5,
) -> list[tuple[str, str, float]]:
    """Return [(card_a_id, card_b_id, label)] with balanced pos/neg pairs.

    Positives: synergy_edges rows with score_type='ability_trigger'.
    Negatives: hard_neg_frac of budget from hard negatives (nearest neighbours
               in embedding space that are NOT synergistic), the rest random.
    Total negatives = neg_ratio × len(positives).
    """
    log.info("Loading synergy pairs (sample=%d)…", sample)
    with get_conn() as conn:
        with conn.cursor() as cur:
            # TABLESAMPLE avoids a full sort — safe even on large tables
            cur.execute("""
                SELECT card_a::text, card_b::text
                FROM synergy_edges TABLESAMPLE SYSTEM(10)
                WHERE score_type = 'ability_trigger'
                LIMIT %s
            """, (sample,))
            positives = [
                (r[0], r[1], 1.0) for r in cur.fetchall()
                if r[0] in embeddings and r[1] in embeddings
            ]

    log.info("  %d positive pairs", len(positives))

    all_ids = list(embeddings.keys())
    pos_set = {(a, b) for a, b, _ in positives}
    n_neg = len(positives) * neg_ratio
    n_hard = int(n_neg * hard_neg_frac)
    n_rand = n_neg - n_hard

    hard_negs = _mine_hard_negatives(positives, embeddings, all_ids, pos_set, n_hard)

    rand_negs: list[tuple[str, str, float]] = []
    attempts = 0
    while len(rand_negs) < n_rand and attempts < n_rand * 10:
        a, b = random.sample(all_ids, 2)
        if (a, b) not in pos_set and (b, a) not in pos_set:
            rand_negs.append((a, b, 0.0))
        attempts += 1

    negatives = hard_negs + rand_negs
    log.info("  %d negative pairs (%d hard, %d random)",
             len(negatives), len(hard_negs), len(rand_negs))
    pairs = positives + negatives
    random.shuffle(pairs)
    return pairs


# ── Phase 1 data ──────────────────────────────────────────────────────────────

class AllCardsDataset(Dataset):
    """Phase 1: every card is a sample; two noisy views are created in the loop."""

    def __init__(self, embeddings: dict):
        self.ids = list(embeddings.keys())
        self.embs = np.stack([embeddings[k] for k in self.ids]).astype(np.float32)

    def __len__(self):
        return len(self.ids)

    def __getitem__(self, idx):
        return torch.from_numpy(self.embs[idx])


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
        return cmd_emb, card_embs, deck["legal_neg_indices"]


# ── Training loops ────────────────────────────────────────────────────────────

def cosine_temperature(epoch: int, n_epochs: int, t_start: float, t_end: float) -> float:
    """Cosine annealing schedule for InfoNCE temperature.

    Returns t_start at epoch 0 and t_end at epoch n_epochs-1.
    High temperature early: soft distribution, easier gradients.
    Low temperature late: sharp distribution, tight positive clustering.
    """
    if n_epochs <= 1:
        return t_end
    return t_end + 0.5 * (t_start - t_end) * (1 + math.cos(math.pi * epoch / (n_epochs - 1)))


def nt_xent_loss(z_i: torch.Tensor, z_j: torch.Tensor, temperature: float = 0.07) -> torch.Tensor:
    """NT-Xent (InfoNCE) loss for a batch of positive pairs.

    z_i, z_j: (B, D) L2-normalised embeddings.
    Each (z_i[k], z_j[k]) is a positive pair; all cross-pairs are negatives.
    """
    B = z_i.size(0)
    z = torch.cat([z_i, z_j], dim=0)                       # (2B, D)
    sim = (z @ z.T) / temperature                           # (2B, 2B)
    # Mask self-similarity (diagonal) so a sample isn't its own negative
    mask = torch.eye(2 * B, device=z.device).bool()
    sim = sim.masked_fill(mask, float("-inf"))
    # For i in [0,B): positive is at i+B; for i in [B,2B): positive is at i-B
    labels = torch.cat([torch.arange(B, 2 * B), torch.arange(B)]).to(z.device)
    return F.cross_entropy(sim, labels)


def train_contrastive_phase(
    model: CardEncoder,
    dataset: AllCardsDataset,
    epochs: int,
    lr: float,
    batch_size: int = 512,
    noise_std: float = 0.05,
    temperature: float = 0.07,
):
    """Phase 1: SimCLR-style contrastive pre-training.

    Two Gaussian-noised views of each card embedding form the positive pair.
    NT-Xent loss over in-batch negatives.  Large batches help significantly
    (more in-batch negatives); default 512 gives 511 negatives per anchor.
    """
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=True,
                        num_workers=0, drop_last=True)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    device = next(model.parameters()).device

    best_loss = float("inf")
    for epoch in range(epochs):
        model.train()
        total_loss = 0.0
        for emb in loader:
            emb = emb.to(device)
            # Create two independent noisy views then re-normalise
            view1 = F.normalize(emb + torch.randn_like(emb) * noise_std, dim=-1)
            view2 = F.normalize(emb + torch.randn_like(emb) * noise_std, dim=-1)

            z1 = model(view1)
            z2 = model(view2)
            loss = nt_xent_loss(z1, z2, temperature)

            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total_loss += loss.item()

        scheduler.step()
        avg = total_loss / len(loader)
        log.info("Phase 1  epoch %d/%d  loss=%.4f  lr=%.2e",
                 epoch + 1, epochs, avg, scheduler.get_last_lr()[0])

        if WANDB_ENABLED:
            wandb.log({"phase": 1, "epoch": epoch + 1, "loss": avg,
                       "lr": scheduler.get_last_lr()[0]})

        if avg < best_loss:
            best_loss = avg
            save_checkpoint(model, "phase1_best")

    save_checkpoint(model, f"phase1_epoch{epochs}")


def train_synergy_phase(
    model: CardEncoder,
    dataset: SynergyDataset,
    epochs: int,
    lr: float,
    batch_size: int = 256,
    label_smoothing: float = 0.1,
    temp_start: float = 0.5,
    temp_end: float = 0.05,
):
    """Phase 2: binary cross-entropy on ability-trigger synergy pairs.

    label_smoothing: ε applied as smooth = label*(1-ε) + ε/2
    (pos → 1-ε/2, neg → ε/2).  Handles noisy regex-derived positive labels.

    temp_start / temp_end: cosine-annealed temperature applied to the cosine
    similarity logit before BCE.  High temperature early smooths the signal;
    low temperature late sharpens the decision boundary.
    """
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=True, num_workers=0)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    device = next(model.parameters()).device

    best_loss = float("inf")
    for epoch in range(epochs):
        temperature = cosine_temperature(epoch, epochs, temp_start, temp_end)
        model.train()
        total_loss = 0.0
        for emb_a, emb_b, labels in loader:
            emb_a  = emb_a.to(device)
            emb_b  = emb_b.to(device)
            labels = labels.to(device)

            # Label smoothing: pull hard 0/1 targets toward centre
            if label_smoothing > 0:
                labels = labels * (1 - label_smoothing) + label_smoothing / 2

            proj_a = model(emb_a)
            proj_b = model(emb_b)
            # Scale cosine similarity by temperature before BCE
            similarity = (proj_a * proj_b).sum(dim=-1) / temperature
            loss = F.binary_cross_entropy_with_logits(similarity, labels)

            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total_loss += loss.item()

        scheduler.step()
        avg = total_loss / len(loader)
        log.info("Phase 2  epoch %d/%d  loss=%.4f  lr=%.2e  temp=%.4f",
                 epoch + 1, epochs, avg, scheduler.get_last_lr()[0], temperature)

        if WANDB_ENABLED:
            wandb.log({"phase": 2, "epoch": epoch + 1, "loss": avg,
                       "lr": scheduler.get_last_lr()[0], "temperature": temperature})

        if avg < best_loss:
            best_loss = avg
            save_checkpoint(model, "phase2_best")

    save_checkpoint(model, f"phase2_epoch{epochs}")


# ── Phase 3: Deck co-occurrence ───────────────────────────────────────────────

def load_color_identities(embeddings: dict[str, np.ndarray]) -> dict[str, frozenset]:
    """Return {card_id: frozenset of color letters} for every embedded card.

    Colorless cards (empty identity) return frozenset(), which is a subset of
    every commander's identity — legal in any deck.
    """
    ids = list(embeddings.keys())
    result: dict[str, frozenset] = {}
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                "SELECT id::text, color_identity FROM cards WHERE id::text = ANY(%s)",
                (ids,),
            )
            for row in cur.fetchall():
                result[row["id"]] = frozenset(row["color_identity"] or [])
    # Any embedded card without a DB row (shouldn't happen) gets colorless
    for card_id in ids:
        result.setdefault(card_id, frozenset())
    return result


def load_decks(embeddings: dict[str, np.ndarray]) -> list[dict]:
    """Load decks from DB, filtering to cards that have embeddings.

    Each deck dict includes `legal_neg_indices`: a numpy int array of indices
    into `list(embeddings.keys())` whose color identity is a subset of the
    commander's color identity.  Phase 3/4 trainers sample negatives from this
    array instead of the full pool, so illegal off-color cards are never used
    as negatives (which would make the learning task trivially easy).
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("""
                SELECT commander_id::text,
                       ARRAY(SELECT unnest(card_ids)::text) AS card_ids
                FROM decks
                WHERE commander_id IS NOT NULL
            """)
            rows = cur.fetchall()

    color_ids = load_color_identities(embeddings)
    all_ids   = list(embeddings.keys())

    # Pre-build index arrays per unique commander color identity to avoid
    # recomputing for commanders that share the same identity.
    _legal_cache: dict[frozenset, np.ndarray] = {}

    def _legal_indices(cmd_ci: frozenset) -> np.ndarray:
        if cmd_ci not in _legal_cache:
            idx = np.array(
                [i for i, cid in enumerate(all_ids)
                 if color_ids.get(cid, frozenset()) <= cmd_ci],
                dtype=np.int64,
            )
            # Fallback: if identity is somehow empty, allow all cards
            _legal_cache[cmd_ci] = idx if len(idx) > 0 else np.arange(len(all_ids), dtype=np.int64)
        return _legal_cache[cmd_ci]

    decks = []
    for row in rows:
        cmd_id = row["commander_id"]
        if cmd_id not in embeddings:
            continue
        card_ids = [str(c) for c in (row["card_ids"] or []) if str(c) in embeddings]
        if len(card_ids) < 10:
            continue
        cmd_ci = color_ids.get(cmd_id, frozenset())
        decks.append({
            "commander_id":      cmd_id,
            "card_ids":          card_ids,
            "color_identity":    cmd_ci,
            "legal_neg_indices": _legal_indices(cmd_ci),
        })

    log.info("Loaded %d decks (%d skipped — commander or cards not embedded)",
             len(decks), len(rows) - len(decks))
    return decks


def train_deck_phase(
    model: CardEncoder,
    dataset: DeckDataset,
    embeddings: dict[str, np.ndarray],
    epochs: int,
    lr: float,
    batch_size: int = 32,
):
    """Phase 3: BPR ranking loss on human Commander decklists.

    For each (commander, deck_card, random_card) triple, push the commander
    embedding closer to actual deck cards than to random cards.

    BPR loss: -log(sigmoid(score_pos - score_neg))
    This teaches relative preference rather than absolute scores, which suits
    the noisy signal from human deckbuilding better than BCE.
    """
    all_ids = list(embeddings.keys())
    all_embs = torch.from_numpy(
        np.stack([embeddings[k] for k in all_ids])
    )  # (N, D)

    loader = DataLoader(
        dataset, batch_size=1, shuffle=True, num_workers=0, collate_fn=lambda x: x[0]
    )
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    device = next(model.parameters()).device
    all_embs = all_embs.to(device)

    best_loss = float("inf")
    for epoch in range(epochs):
        model.train()
        total_loss = 0.0
        n_batches = 0

        for cmd_emb, card_embs, legal_neg_idx in loader:
            cmd_emb  = cmd_emb.to(device)   # (D,)
            card_embs = card_embs.to(device)  # (K, D)

            if card_embs.size(0) < 2:
                continue

            # Project commander and deck cards
            z_cmd  = model(cmd_emb.unsqueeze(0))            # (1, D')
            z_pos  = model(card_embs)                       # (K, D')

            # Sample K random negatives restricted to the commander's color identity
            K = card_embs.size(0)
            neg_pool = legal_neg_idx.numpy() if hasattr(legal_neg_idx, "numpy") else legal_neg_idx
            chosen = np.random.choice(neg_pool, size=K, replace=True)
            neg_idx = torch.from_numpy(chosen).to(device)
            z_neg  = model(all_embs[neg_idx])               # (K, D')

            # BPR: cosine similarity between commander and pos/neg cards
            score_pos = (z_cmd * z_pos).sum(dim=-1)         # (K,)
            score_neg = (z_cmd * z_neg).sum(dim=-1)         # (K,)

            loss = -F.logsigmoid(score_pos - score_neg).mean()

            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()

            total_loss += loss.item()
            n_batches  += 1

        scheduler.step()
        avg = total_loss / max(n_batches, 1)
        log.info("Phase 3  epoch %d/%d  loss=%.4f  lr=%.2e",
                 epoch + 1, epochs, avg, scheduler.get_last_lr()[0])

        if WANDB_ENABLED:
            wandb.log({"phase": 3, "epoch": epoch + 1, "loss": avg,
                       "lr": scheduler.get_last_lr()[0]})

        if avg < best_loss:
            best_loss = avg
            save_checkpoint(model, "phase3_best")

    save_checkpoint(model, f"phase3_epoch{epochs}")


# ── Phase 4: Autoregressive deck construction ─────────────────────────────────

def train_deck_constructor_phase(
    model: DeckConstructor,
    dataset: DeckDataset,
    embeddings: dict[str, np.ndarray],
    epochs: int,
    lr: float,
    n_neg: int = 64,
    positions_per_deck: int = 10,
    temp_start: float = 0.5,
    temp_end: float = 0.05,
    freeze_encoder: bool = True,
    encoder_lr_scale: float = 0.1,
):
    """Phase 4: autoregressive deck construction via transformer decoder + InfoNCE.

    For each deck, randomly sample `positions_per_deck` positions K (1 ≤ K < deck_len).
    At each position the model sees [commander, cards[0:K]] and must rank cards[K]
    above n_neg random cards drawn from the full 33k pool.

    InfoNCE loss over (1 + n_neg) candidates — sharper than BPR, better suited to
    the multi-candidate ranking setting.

    Temperature is cosine-annealed from temp_start (epoch 1) to temp_end (final epoch).
    Starting high softens the distribution early to prevent mode collapse; ending low
    sharpens the geometry for precise nearest-neighbour retrieval at inference time.

    The full card pool is pre-projected at the start of each epoch so negative
    sampling is O(1); projections are refreshed each epoch as encoder weights update.

    When freeze_encoder=False, the encoder is updated at lr * encoder_lr_scale
    (default 0.1×) to prevent it from collapsing Phase 3 representations while the
    decoder learns quickly at the full lr.
    """
    if freeze_encoder:
        model.card_encoder.requires_grad_(False)
        log.info("Phase 4: card_encoder frozen — only decoder + scorer will be trained")
        trainable = [p for p in model.parameters() if p.requires_grad]
        optimizer = torch.optim.AdamW(trainable, lr=lr, weight_decay=1e-4)
    else:
        encoder_lr = lr * encoder_lr_scale
        log.info(
            "Phase 4: encoder unfrozen — encoder lr=%.2e (%.0f%% of decoder lr=%.2e)",
            encoder_lr, encoder_lr_scale * 100, lr,
        )
        optimizer = torch.optim.AdamW([
            {"params": model.card_encoder.parameters(), "lr": encoder_lr},
            {"params": list(model.decoder.parameters()) + list(model.scorer.parameters()), "lr": lr},
        ], weight_decay=1e-4)

    all_ids = list(embeddings.keys())
    all_raw = torch.from_numpy(
        np.stack([embeddings[k] for k in all_ids]).astype(np.float32)
    )

    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    device = next(model.parameters()).device
    all_raw = all_raw.to(device)

    best_loss = float("inf")
    for epoch in range(epochs):
        temperature = cosine_temperature(epoch, epochs, temp_start, temp_end)

        # ── Pre-project the full card pool for fast negative sampling ──────────
        model.eval()
        with torch.no_grad():
            all_proj = torch.cat([
                model.card_encoder(all_raw[i: i + 512])
                for i in range(0, all_raw.size(0), 512)
            ], dim=0)  # (N, 256), L2-normalised, no grad
        model.train()

        total_loss = 0.0
        n_steps = 0
        deck_indices = list(range(len(dataset)))
        random.shuffle(deck_indices)

        for idx in deck_indices:
            cmd_raw, card_raw, legal_neg_idx = dataset[idx]  # (384,), (K, 384), int array
            card_raw = card_raw.to(device)
            cmd_raw  = cmd_raw.to(device)
            K = card_raw.size(0)
            if K < 2:
                continue

            # Sample positions: always K ≥ 1 so there is at least one card in context
            n_pos = min(positions_per_deck, K - 1)
            positions = random.sample(range(1, K), n_pos)

            # Legal negative pool: indices into all_proj restricted to commander's color identity
            legal_pool = legal_neg_idx if isinstance(legal_neg_idx, np.ndarray) else legal_neg_idx.numpy()

            deck_loss = torch.tensor(0.0, device=device)
            for pos in positions:
                # Project context + target through encoder (gradients flow)
                z_cmd     = model.card_encoder(cmd_raw.unsqueeze(0))          # (1, 256)
                z_context = model.card_encoder(card_raw[:pos])                # (pos, 256)
                z_target  = model.card_encoder(card_raw[pos].unsqueeze(0))   # (1, 256)

                # Random negatives from the pre-projected pool, color-identity filtered
                chosen  = np.random.choice(legal_pool, size=n_neg, replace=True)
                neg_idx = torch.from_numpy(chosen).to(device)
                z_neg   = all_proj[neg_idx].detach()                          # (n_neg, 256)

                # Candidate set: [positive, neg_0, ..., neg_{n_neg-1}]
                candidates = torch.cat([z_target, z_neg], dim=0)             # (1+n_neg, 256)

                # Score via transformer decoder
                scores = model(
                    z_cmd,                        # (1, 256)
                    z_context.unsqueeze(0),       # (1, pos, 256)
                    candidates.unsqueeze(0),      # (1, 1+n_neg, 256)
                ).squeeze(0)                      # (1+n_neg,)

                # InfoNCE: positive is always index 0
                deck_loss = deck_loss + (-F.log_softmax(scores / temperature, dim=0)[0])

            avg_deck_loss = deck_loss / n_pos
            optimizer.zero_grad()
            avg_deck_loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total_loss += avg_deck_loss.item()
            n_steps += 1

        scheduler.step()
        avg = total_loss / max(n_steps, 1)
        log.info("Phase 4  epoch %d/%d  loss=%.4f  lr=%.2e  temp=%.4f",
                 epoch + 1, epochs, avg, scheduler.get_last_lr()[0], temperature)

        if WANDB_ENABLED:
            wandb.log({"phase": 4, "epoch": epoch + 1, "loss": avg,
                       "lr": scheduler.get_last_lr()[0], "temperature": temperature})

        if avg < best_loss:
            best_loss = avg
            save_checkpoint(model, "phase4_best")

    save_checkpoint(model, f"phase4_epoch{epochs}")


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
    if not path.exists():
        log.warning("Checkpoint not found: %s — starting from scratch", path)
        return model

    state = torch.load(path, map_location=device)

    # If we're loading a DeckConstructor checkpoint into a CardEncoder, extract
    # just the card_encoder sub-module weights (keys prefixed "card_encoder.").
    model_keys = set(model.state_dict().keys())
    if not model_keys.issubset(set(state.keys())):
        prefix = "card_encoder."
        extracted = {k[len(prefix):]: v for k, v in state.items() if k.startswith(prefix)}
        if extracted and model_keys.issubset(set(extracted.keys())):
            log.info("Extracting card_encoder weights from DeckConstructor checkpoint: %s", path)
            state = extracted

    model.load_state_dict(state)
    log.info("Loaded checkpoint: %s", path)
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
    parser.add_argument("--hard-neg-frac", type=float, default=0.5,
                        help="Fraction of negatives that are hard (nearest-neighbour) vs random")
    parser.add_argument("--sample", type=int, default=500_000,
                        help="Max positive pairs to sample from synergy_edges")
    parser.add_argument("--label-smoothing", type=float, default=0.1,
                        help="Label smoothing epsilon (0=off); pos→1-ε/2, neg→ε/2")
    parser.add_argument("--noise", type=float, default=0.05,
                        help="Phase 1: std of Gaussian noise added to create augmented views")
    parser.add_argument("--temperature", type=float, default=0.07,
                        help="Phase 1: NT-Xent temperature (lower=sharper contrast)")
    parser.add_argument("--temp-start", type=float, default=0.5,
                        help="Phase 2/4: initial InfoNCE temperature at epoch 1 "
                             "(high = soft distribution, easier early gradients)")
    parser.add_argument("--temp-end", type=float, default=0.05,
                        help="Phase 2/4: final InfoNCE temperature at last epoch "
                             "(low = sharp distribution, tight positive clustering)")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from latest checkpoint (phase2→phase2_best, "
                             "phase1→phase1_best; falls back to previous phase if not found)")
    parser.add_argument("--freeze-encoder", action="store_true", default=True,
                        dest="freeze_encoder",
                        help="Phase 4: freeze card_encoder weights so the decoder learns "
                             "to use fixed Phase 3 representations without collapsing them "
                             "(default: True; use --no-freeze-encoder to disable)")
    parser.add_argument("--no-freeze-encoder", action="store_false", dest="freeze_encoder")
    parser.add_argument("--encoder-lr-scale", type=float, default=0.1,
                        help="Phase 4 --no-freeze-encoder: encoder lr as fraction of decoder lr "
                             "(default 0.1 — encoder updates 10× slower to prevent collapse)")
    args = parser.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    log.info("Training phase %d on %s", args.phase, device)

    if WANDB_ENABLED:
        wandb.init(
            project=os.environ.get("WANDB_PROJECT", "edh-builder"),
            config=vars(args),
        )

    if args.phase == 1:
        embeddings = load_embeddings()
        if not embeddings:
            log.error("No embeddings found — run the ingest pipeline first.")
            return

        dataset = AllCardsDataset(embeddings)
        log.info("Dataset: %d cards", len(dataset))

        model = CardEncoder().to(device)
        if args.resume:
            load_checkpoint(model, "phase1_best", device)

        train_contrastive_phase(
            model, dataset, args.epochs, args.lr, args.batch_size,
            noise_std=args.noise, temperature=args.temperature,
        )

    elif args.phase == 2:
        embeddings = load_embeddings()
        if not embeddings:
            log.error("No embeddings found — run the ingest pipeline first.")
            return

        pairs = load_synergy_pairs(
            embeddings,
            neg_ratio=args.neg_ratio,
            sample=args.sample,
            hard_neg_frac=args.hard_neg_frac,
        )
        if not pairs:
            log.error("No synergy pairs found — run compute_synergy stage first.")
            return

        dataset = SynergyDataset(pairs, embeddings)
        log.info("Dataset: %d pairs", len(dataset))

        model = CardEncoder().to(device)
        if args.resume:
            # Try phase2_best first; fall back to phase1_best (warm start from Phase 1)
            if (CHECKPOINT_DIR / "phase2_best.pt").exists():
                load_checkpoint(model, "phase2_best", device)
            else:
                log.info("No phase2_best found — loading phase1_best as warm start")
                load_checkpoint(model, "phase1_best", device)

        train_synergy_phase(
            model, dataset, args.epochs, args.lr, args.batch_size,
            label_smoothing=args.label_smoothing,
            temp_start=args.temp_start,
            temp_end=args.temp_end,
        )

    elif args.phase == 3:
        embeddings = load_embeddings()
        if not embeddings:
            log.error("No embeddings found — run the ingest pipeline first.")
            return

        decks = load_decks(embeddings)
        if not decks:
            log.error("No decks found — run import_decklists.py first.")
            return

        dataset = DeckDataset(decks, embeddings)
        log.info("Dataset: %d decks", len(dataset))

        model = CardEncoder().to(device)
        if args.resume:
            if (CHECKPOINT_DIR / "phase3_best.pt").exists():
                load_checkpoint(model, "phase3_best", device)
            else:
                log.info("No phase3_best found — loading phase2_best as warm start")
                load_checkpoint(model, "phase2_best", device)

        train_deck_phase(
            model, dataset, embeddings, args.epochs, args.lr, args.batch_size,
        )

    elif args.phase == 4:
        embeddings = load_embeddings()
        if not embeddings:
            log.error("No embeddings found — run the ingest pipeline first.")
            return

        decks = load_decks(embeddings)
        if not decks:
            log.error("No decks found — run import_decklists.py first.")
            return

        dataset = DeckDataset(decks, embeddings)
        log.info("Dataset: %d decks", len(dataset))

        model = DeckConstructor().to(device)
        if args.resume and (CHECKPOINT_DIR / "phase4_best.pt").exists():
            load_checkpoint(model, "phase4_best", device)
        else:
            # Warm-start the card_encoder from Phase 3 weights
            phase3_encoder = CardEncoder().to(device)
            if (CHECKPOINT_DIR / "phase3_best.pt").exists():
                load_checkpoint(phase3_encoder, "phase3_best", device)
                model.card_encoder.load_state_dict(phase3_encoder.state_dict())
                log.info("Warm-started card_encoder from phase3_best")
            else:
                log.warning("No phase3_best found — card_encoder starts from scratch")

        train_deck_constructor_phase(
            model, dataset, embeddings, args.epochs, args.lr,
            n_neg=64, positions_per_deck=10,
            temp_start=args.temp_start,
            temp_end=args.temp_end,
            freeze_encoder=args.freeze_encoder,
            encoder_lr_scale=args.encoder_lr_scale,
        )

    else:
        log.warning("Phase %d not yet implemented.", args.phase)

    if WANDB_ENABLED:
        wandb.finish()


if __name__ == "__main__":
    main()
