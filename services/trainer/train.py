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

Phase 3 – Commander BPR (commanders artifact)
    BPR ranking loss on the commanders artifact deck entries.  For each
    (commander, positive_card, negative_card) triple, pushes the commander
    embedding closer to cards that genuinely synergise with it than to random
    color-legal cards.  Ground truth comes from synergy_edges (ability_trigger
    and commander_value) stored in mtg_commanders.pt — no human decklists
    required.
"""

from __future__ import annotations

import argparse

import json
import logging
import math
import os
import random
import shutil
import sys
from pathlib import Path

import numpy as np
import psycopg2
import psycopg2.extras
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset, Sampler

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

CHECKPOINT_DIR = Path(os.environ.get("CHECKPOINT_DIR", "/checkpoints"))
DATABASE_URL = os.environ.get("DATABASE_URL", "")
EMBEDDING_MODEL = os.environ.get(
    "EMBEDDING_MODEL", "sentence-transformers/all-mpnet-base-v2"
)

try:
    import wandb

    WANDB_ENABLED = bool(os.environ.get("WANDB_API_KEY"))
except ImportError:
    WANDB_ENABLED = False


def _wandb_log(data: dict) -> None:
    """wandb.log with network-error tolerance.

    W&B uses a local socket to its service process.  On Windows a transient
    network event (VPN drop, sleep/resume, WinError 64) can break that socket
    mid-run and raise ConnectionResetError, killing training.  Swallow those
    errors so the training loop continues; the run will be incomplete in the
    W&B UI but the checkpoint is unaffected.
    """
    try:
        wandb.log(data)
    except Exception as exc:  # noqa: BLE001
        log.warning("wandb.log failed (network error?) — skipping: %s", exc)


def _wandb_summary(data: dict) -> None:
    """Write end-of-run summary metrics to wandb.run.summary.

    Summary values appear prominently in the W&B runs table and are not
    tied to a step — suitable for best_loss, best_epoch, stopped_early, etc.
    """
    if not WANDB_ENABLED:
        return
    try:
        wandb.run.summary.update(data)
    except Exception as exc:  # noqa: BLE001
        log.warning("wandb.summary failed: %s", exc)


# ── DB helpers ────────────────────────────────────────────────────────────────


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def load_embeddings(model_name: str = EMBEDDING_MODEL) -> dict[str, np.ndarray]:
    """Return {card_id (str): np.ndarray} for all embedded cards."""
    log.info("Loading embeddings (model=%s)…", model_name)
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                """
                SELECT card_id::text, embedding
                FROM card_embeddings
                WHERE model = %s
            """,
                (model_name,),
            )
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


# ── DB loading helpers (Phase 2) ──────────────────────────────────────────────


def _mine_hard_negatives(
    positives: list[tuple],
    embeddings: dict,
    all_ids: list[str],
    pos_set: set,
    n_hard: int,
    top_k: int = 200,
) -> list[tuple[str, str, float]]:
    """Return hard negatives: cards semantically similar to card_a but not synergistic."""
    log.info("Mining %d hard negatives (top_k=%d)…", n_hard, top_k)
    id_to_idx = {card_id: i for i, card_id in enumerate(all_ids)}
    emb_matrix = np.stack([embeddings[k] for k in all_ids])
    norms = np.linalg.norm(emb_matrix, axis=1, keepdims=True)
    normed = (emb_matrix / np.maximum(norms, 1e-8)).astype(np.float32)

    unique_a = list({a for a, _, _ in positives})
    random.shuffle(unique_a)
    per_anchor = max(1, -(-n_hard // max(len(unique_a), 1)))

    hard_negs: list[tuple[str, str, float]] = []
    for card_a in unique_a:
        if len(hard_negs) >= n_hard:
            break
        a_vec = normed[id_to_idx[card_a]]
        sims = normed @ a_vec
        ranked = np.argsort(sims)[::-1]
        collected = 0
        for idx in ranked[1: top_k + 1]:
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
    combo_sample: int = 200_000,
    commander_value_sample: int = 200_000,
) -> list[tuple[str, str, float]]:
    """Return [(card_a_id, card_b_id, label)] with balanced pos/neg pairs.

    Positives: ability_trigger, commander_value, and combo_package edges from
    synergy_edges.  Negatives: hard (nearest-neighbour) + random.
    """
    log.info(
        "Loading synergy pairs (ability_trigger=%d, combo=%d, commander_value=%d)…",
        sample, combo_sample, commander_value_sample,
    )
    with get_conn() as conn:
        with conn.cursor() as cur:
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

            if combo_sample > 0:
                cur.execute("""
                    SELECT a.card_id::text, b.card_id::text
                    FROM combo_package_cards a
                    JOIN combo_package_cards b
                      ON a.combo_package_id = b.combo_package_id
                     AND a.card_id < b.card_id
                    WHERE a.card_id IS NOT NULL
                      AND b.card_id IS NOT NULL
                      AND a.is_template = FALSE
                      AND b.is_template = FALSE
                    LIMIT %s
                """, (combo_sample,))
                combo_pairs = [
                    (r[0], r[1], 1.0) for r in cur.fetchall()
                    if r[0] in embeddings and r[1] in embeddings
                ]
                log.info("  + %d combo_package pairs", len(combo_pairs))
                positives = positives + combo_pairs

            if commander_value_sample > 0:
                cur.execute("""
                    SELECT card_a::text, card_b::text, score
                    FROM synergy_edges TABLESAMPLE SYSTEM(10)
                    WHERE score_type = 'commander_value'
                    LIMIT %s
                """, (commander_value_sample,))
                cv_pairs = [
                    (r[0], r[1], float(r[2])) for r in cur.fetchall()
                    if r[0] in embeddings and r[1] in embeddings
                ]
                log.info("  + %d commander_value pairs", len(cv_pairs))
                positives = positives + cv_pairs

    log.info("  %d total positive pairs", len(positives))

    all_ids = list(embeddings.keys())
    pos_set = {(a, b) for a, b, _ in positives}
    n_neg   = len(positives) * neg_ratio
    n_hard  = int(n_neg * hard_neg_frac)
    n_rand  = n_neg - n_hard

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


def load_color_identities(embeddings: dict[str, np.ndarray]) -> dict[str, frozenset]:
    """Return {card_id: frozenset of color letters} for every embedded card."""
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
    for card_id in ids:
        result.setdefault(card_id, frozenset())
    return result



# ── Phase 1 data ──────────────────────────────────────────────────────────────


class AllCardsDataset(Dataset):
    """Phase 1 co-occurrence: every card is a sample; two noisy views created in the loop."""

    def __init__(self, embeddings: dict):
        self.ids = list(embeddings.keys())
        self.embs = np.stack([embeddings[k] for k in self.ids]).astype(np.float32)

    def __len__(self):
        return len(self.ids)

    def __getitem__(self, idx):
        return torch.from_numpy(self.embs[idx])


class ColorBucketBatchSampler(Sampler):
    """Batch sampler yielding batches with ~50% same-color-identity cards.

    Harder in-batch negatives for NT-Xent: by packing same-color cards into
    each batch the model must distinguish role-similar cards (e.g. two {W}
    removal spells) rather than trivially separating a Forest from a Lightning
    Bolt.  The other 50% is random to maintain coverage of the full card pool.
    """

    def __init__(
        self,
        n: int,
        color_buckets: dict[str, list[int]],
        batch_size: int,
    ):
        self.n = n
        self.batch_size = batch_size
        self.all_indices = list(range(n))
        # Only keep buckets large enough to sample a half-batch from
        self.buckets = [idxs for idxs in color_buckets.values() if len(idxs) >= 4]

    def __len__(self) -> int:
        return self.n // self.batch_size

    def __iter__(self):
        half = self.batch_size // 2
        all_shuffled = self.all_indices[:]
        random.shuffle(all_shuffled)
        for start in range(0, len(all_shuffled) - half + 1, half):
            rand_half = all_shuffled[start : start + half]
            if len(rand_half) < half:
                break
            if self.buckets:
                bucket = random.choice(self.buckets)
                color_half = random.choices(bucket, k=half)
            else:
                color_half = random.sample(self.all_indices, half)
            batch = rand_half + color_half
            random.shuffle(batch)
            yield batch


# ── Model ─────────────────────────────────────────────────────────────────────


# Model architectures are canonical in shared/composition/models.py (#152).
# The path shim below lets the non-Docker Windows trainer resolve shared/.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from composition.models import BilinearSynergyHead, CardEncoder  # noqa: E402


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


class SynergyRelationDataset(Dataset):
    """Phase 2 bilinear: positive pairs tagged with relation type index.

    Each item returns (emb_a, emb_b, relation_idx) where relation_idx is
    an int indexing into BilinearSynergyHead.RELATIONS.  Used by
    train_bilinear_phase to route each pair to the correct W_r matrix.
    """

    def __init__(
        self,
        pairs: list[tuple[str, str, int]],
        embeddings: dict[str, np.ndarray],
    ):
        self.pairs = pairs
        self.embeddings = embeddings

    def __len__(self) -> int:
        return len(self.pairs)

    def __getitem__(self, idx: int):
        card_a, card_b, rel = self.pairs[idx]
        emb_a = torch.from_numpy(self.embeddings[card_a])
        emb_b = torch.from_numpy(self.embeddings[card_b])
        return emb_a, emb_b, torch.tensor(rel, dtype=torch.long)


# ── Training loops ────────────────────────────────────────────────────────────


def cosine_temperature(
    epoch: int, n_epochs: int, t_start: float, t_end: float
) -> float:
    """Cosine annealing schedule for InfoNCE temperature.

    Returns t_start at epoch 0 and t_end at epoch n_epochs-1.
    High temperature early: soft distribution, easier gradients.
    Low temperature late: sharp distribution, tight positive clustering.
    """
    if n_epochs <= 1:
        return t_end
    return t_end + 0.5 * (t_start - t_end) * (
        1 + math.cos(math.pi * epoch / (n_epochs - 1))
    )


def nt_xent_loss(
    z_i: torch.Tensor, z_j: torch.Tensor, temperature: float = 0.07
) -> torch.Tensor:
    """NT-Xent (InfoNCE) loss for a batch of positive pairs.

    z_i, z_j: (B, D) L2-normalised embeddings.
    Each (z_i[k], z_j[k]) is a positive pair; all cross-pairs are negatives.
    """
    B = z_i.size(0)
    z = torch.cat([z_i, z_j], dim=0)  # (2B, D)
    sim = (z @ z.T) / temperature  # (2B, 2B)
    # Mask self-similarity (diagonal) so a sample isn't its own negative
    mask = torch.eye(2 * B, device=z.device).bool()
    sim = sim.masked_fill(mask, float("-inf"))
    # For i in [0,B): positive is at i+B; for i in [B,2B): positive is at i-B
    labels = torch.cat([torch.arange(B, 2 * B), torch.arange(B)]).to(z.device)
    return F.cross_entropy(sim, labels)


def bilinear_nt_xent_loss(
    z_a: torch.Tensor,
    z_b: torch.Tensor,
    head: BilinearSynergyHead,
    relation: int | str,
    temperature: float = 0.07,
) -> torch.Tensor:
    """Asymmetric bilinear InfoNCE loss for a batch of positive pairs.

    z_a, z_b: (B, D) L2-normalised encoder outputs.

    Score matrix: S[i, j] = z_a[i]^T W_r z_b[j] / temperature.
    The diagonal (i == j) are the positive pairs; all other entries in each
    row are in-batch negatives.  Asymmetric formulation (one direction only)
    is natural for directed relations (ability_trigger, decomposed_candidates)
    and avoids double-counting for symmetric ones (effect_peer, combo).
    """
    B = z_a.size(0)
    sim = head.score_matrix(z_a, z_b, relation) / temperature  # (B, B)
    labels = torch.arange(B, device=z_a.device)
    return F.cross_entropy(sim, labels)


def train_contrastive_phase(
    model: CardEncoder,
    dataset: AllCardsDataset,
    epochs: int,
    lr: float,
    batch_size: int = 512,
    noise_std: float = 0.05,
    temperature: float = 0.07,
    checkpoint_prefix: str = "phase",
    staple_pairs: list[tuple[int, int, float]] | None = None,
    staple_pair_weight: float = 0.5,
    color_buckets: dict[str, list[int]] | None = None,
):
    """Phase 1: SimCLR-style contrastive pre-training with optional role-pair augmentation.

    Core loss: two Gaussian-noised views of each card embedding form the positive
    pair; NT-Xent over in-batch negatives.  Large batches help significantly
    (more in-batch negatives); default 512 gives 511 negatives per anchor.

    Optional extensions (activated when artifact supplies the data):

    staple_pairs — list of (a_idx, b_idx, cmc_weight) from EDHREC staple
        categories (mana_rocks, removal, sweeper, …).  Each training step adds
        a second NT-Xent term on a randomly sampled batch of these pairs, scaled
        by ``staple_pair_weight`` (default 0.5) and the per-pair CMC weight.
        This bootstraps role geometry before Phase 2 so that Sol Ring and
        Arcane Signet are already neighbours when synergy training begins.

    color_buckets — {color_identity_key: [card_indices]}.  When provided,
        the DataLoader uses ColorBucketBatchSampler: ~50% of each batch comes
        from the same color identity, making in-batch negatives harder (the
        model must distinguish within-color role differences, not just
        color differences).
    """
    if color_buckets is not None:
        sampler = ColorBucketBatchSampler(len(dataset), color_buckets, batch_size)
        loader = DataLoader(dataset, batch_sampler=sampler, num_workers=0)
        log.info(
            "Phase 1: color-scoped batch sampler active (%d color buckets)",
            len([b for b in color_buckets.values() if len(b) >= 4]),
        )
    else:
        loader = DataLoader(
            dataset, batch_size=batch_size, shuffle=True, num_workers=0, drop_last=True
        )

    if staple_pairs:
        staple_arr = np.array([(a, b) for a, b, _ in staple_pairs], dtype=np.int32)
        staple_weights = np.array([w for _, _, w in staple_pairs], dtype=np.float32)
        log.info(
            "Phase 1: %d staple pairs loaded (weight=%.2f × CMC weight per pair)",
            len(staple_pairs), staple_pair_weight,
        )

    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    device = next(model.parameters()).device

    best_loss = float("inf")
    best_epoch = 0
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

            # Staple role-pair step: interleave NT-Xent on explicit same-role
            # pairs.  Each pair has a CMC-based weight (same CMC=1.0,
            # 3+-apart=0.5).  The batch mean weight scales the staple term
            # so high-CMC-variance pairs contribute proportionally less.
            if staple_pairs:
                batch_n = emb.size(0)
                chosen = np.random.choice(len(staple_pairs), size=batch_n, replace=True)
                ea = torch.from_numpy(dataset.embs[staple_arr[chosen, 0]]).to(device)
                eb = torch.from_numpy(dataset.embs[staple_arr[chosen, 1]]).to(device)
                za = model(ea)
                zb = model(eb)
                mean_cmc_w = float(staple_weights[chosen].mean())
                loss = loss + staple_pair_weight * mean_cmc_w * nt_xent_loss(za, zb, temperature)

            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total_loss += loss.item()

        scheduler.step()
        avg = total_loss / len(loader)
        log.info(
            "Phase 1  epoch %d/%d  loss=%.4f  lr=%.2e",
            epoch + 1,
            epochs,
            avg,
            scheduler.get_last_lr()[0],
        )

        if WANDB_ENABLED:
            _wandb_log(
                {
                    "phase": 1,
                    "epoch": epoch + 1,
                    "loss": avg,
                    "lr": scheduler.get_last_lr()[0],
                }
            )

        if avg < best_loss:
            best_loss = avg
            best_epoch = epoch + 1
            save_checkpoint(model, checkpoint_prefix + "1_best")

    save_checkpoint(model, f"{checkpoint_prefix}1_epoch{epochs}")
    return {"phase": 1, "best_loss": best_loss, "best_epoch": best_epoch,
            "final_epoch": epochs, "stopped_early": False}


def train_synergy_phase(
    model: CardEncoder,
    dataset: SynergyDataset,
    epochs: int,
    lr: float,
    batch_size: int = 256,
    temp_start: float = 0.3,
    temp_end: float = 0.07,
    encoder_lr_scale: float = 1.0,
    checkpoint_prefix: str = "phase",
    staple_pairs: list[tuple[int, int, float]] | None = None,
    staple_pair_weight: float = 0.5,
    staple_embs: np.ndarray | None = None,
):
    """Phase 2: NT-Xent (InfoNCE) on positive ability-trigger synergy pairs.

    Positive pairs (label > 0.5) are treated the same way Phase 1 treats
    reprint pairs: NT-Xent with cosine-annealed temperature.  In-batch
    negatives are mined automatically — no explicit negative pairs needed.

    This replaces the BCE formulation which had a degenerate minimum: BCE on
    cosine similarity in [-1, +1] produces near-random gradients when starting
    from Phase 1 geometry (epoch-1 loss ≈ log(2) ≈ 0.693), and the 540k
    gradient steps at low lr accumulate to corrupt Phase 1 clusters by
    amplifying surface-text features already present in the input embeddings.

    NT-Xent has no degenerate collapse solution — the in-batch negatives always
    provide contrastive signal — and temperature annealing from temp_start to
    temp_end gives an easy-gradient warmup before sharpening the clusters.

    encoder_lr_scale: multiply lr by this factor for all parameters.
    Default 1.0 (no scaling).  Set to 0.1 via run.ps1 as a secondary
    safeguard so the encoder drifts slowly from the Phase 1 optimum.
    """
    # Filter to positive pairs only — NT-Xent mines its own in-batch negatives.
    pos_pairs = [(a, b, l) for a, b, l in dataset.pairs if l > 0.5]
    log.info(
        "Phase 2: %d positive pairs retained (of %d total, %.1f%%)",
        len(pos_pairs),
        len(dataset),
        100.0 * len(pos_pairs) / max(len(dataset), 1),
    )
    pos_dataset = SynergyDataset(pos_pairs, dataset.embeddings)
    loader = DataLoader(
        pos_dataset, batch_size=batch_size, shuffle=True, num_workers=0, drop_last=True
    )
    log.info(
        "Phase 2 loader: %d batches/epoch  batch_size=%d  temp %.3f→%.3f",
        len(loader),
        batch_size,
        temp_start,
        temp_end,
    )

    effective_lr = lr * encoder_lr_scale
    if encoder_lr_scale != 1.0:
        log.info(
            "Phase 2: encoder_lr_scale=%.2f  effective lr=%.2e  (base lr=%.2e)",
            encoder_lr_scale,
            effective_lr,
            lr,
        )

    staple_arr: np.ndarray | None = None
    staple_weights_arr: np.ndarray | None = None
    if staple_pairs:
        staple_arr = np.array([(a, b) for a, b, _ in staple_pairs], dtype=np.int32)
        staple_weights_arr = np.array([w for _, _, w in staple_pairs], dtype=np.float32)
        log.info(
            "Phase 2: %d staple role-anchor pairs loaded (weight=%.2f × CMC weight per pair)",
            len(staple_pairs), staple_pair_weight,
        )

    optimizer = torch.optim.AdamW(
        model.parameters(), lr=effective_lr, weight_decay=1e-4
    )
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    device = next(model.parameters()).device

    best_loss = float("inf")
    best_epoch = 0
    for epoch in range(epochs):
        temperature = cosine_temperature(epoch, epochs, temp_start, temp_end)
        model.train()
        total_loss = 0.0
        for emb_a, emb_b, _ in loader:
            emb_a = emb_a.to(device)
            emb_b = emb_b.to(device)

            z_a = model(emb_a)
            z_b = model(emb_b)
            loss = nt_xent_loss(z_a, z_b, temperature)

            if staple_arr is not None and staple_embs is not None:
                batch_n = emb_a.size(0)
                chosen = np.random.choice(len(staple_pairs), size=batch_n, replace=True)
                ea = torch.from_numpy(staple_embs[staple_arr[chosen, 0]]).to(device)
                eb = torch.from_numpy(staple_embs[staple_arr[chosen, 1]]).to(device)
                za = model(ea)
                zb = model(eb)
                mean_cmc_w = float(staple_weights_arr[chosen].mean())
                loss = loss + staple_pair_weight * mean_cmc_w * nt_xent_loss(za, zb, temperature)

            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total_loss += loss.item()

        scheduler.step()
        avg = total_loss / len(loader)
        log.info(
            "Phase 2  epoch %d/%d  loss=%.4f  lr=%.2e  temp=%.4f",
            epoch + 1,
            epochs,
            avg,
            scheduler.get_last_lr()[0],
            temperature,
        )

        if WANDB_ENABLED:
            _wandb_log(
                {
                    "phase": 2,
                    "epoch": epoch + 1,
                    "loss": avg,
                    "lr": scheduler.get_last_lr()[0],
                    "temperature": temperature,
                }
            )

        if avg < best_loss:
            best_loss = avg
            best_epoch = epoch + 1
            save_checkpoint(model, checkpoint_prefix + "2_best")

    save_checkpoint(model, f"{checkpoint_prefix}2_epoch{epochs}")
    return {"phase": 2, "best_loss": best_loss, "best_epoch": best_epoch,
            "final_epoch": epochs, "stopped_early": False}


# ── Phase 2 bilinear (Option B) ───────────────────────────────────────────────


def load_relation_pairs_from_artifact(
    data: dict,
    combo_sample: int = 200_000,
    effect_peer_sample: int = 200_000,
    ability_trigger_sample: int = 200_000,
    decomposed_sample: int = 200_000,
) -> dict[str, list[tuple[str, str]]]:
    """Load positive pairs grouped by relation type from the training artifact.

    Returns {relation_name: [(card_a_id, card_b_id), …]} for each relation
    present in the artifact.  Used by train_bilinear_phase to build
    per-relation DataLoaders.

    Relations loaded:
        combo                 — from artifact["synergy"] (label > 0.5 = positive)
        effect_peer           — from artifact["effect_peer"]
        ability_trigger       — from artifact["ability_trigger"]
        decomposed_candidates — from artifact["decomposed_candidates"]
    """
    card_ids = data["card_ids"]
    rng = random.Random(42)
    result: dict[str, list[tuple[str, str]]] = {}

    # combo — synergy key contains combo_package edges; label > 0.5 = positive
    syn = data.get("synergy")
    if syn is not None:
        combo_pairs: list[tuple[str, str]] = [
            (card_ids[a], card_ids[b])
            for a, b, l in zip(
                syn["a_idx"].tolist(), syn["b_idx"].tolist(), syn["labels"].tolist()
            )
            if l > 0.5
        ]
        rng.shuffle(combo_pairs)
        if len(combo_pairs) > combo_sample:
            combo_pairs = combo_pairs[:combo_sample]
        result["combo"] = combo_pairs
        log.info("load_relation_pairs: %d combo pairs", len(combo_pairs))

    # effect_peer — symmetric peer pairs by (trigger_event, effect_class)
    ep = data.get("effect_peer")
    if ep is not None:
        ep_pairs: list[tuple[str, str]] = [
            (card_ids[a], card_ids[b])
            for a, b in zip(ep["a_idx"].tolist(), ep["b_idx"].tolist())
        ]
        rng.shuffle(ep_pairs)
        if len(ep_pairs) > effect_peer_sample:
            ep_pairs = ep_pairs[:effect_peer_sample]
        result["effect_peer"] = ep_pairs
        log.info("load_relation_pairs: %d effect_peer pairs", len(ep_pairs))

    # ability_trigger — fine oracle-text pattern edges (producer → consumer)
    at = data.get("ability_trigger")
    if at is not None:
        at_pairs: list[tuple[str, str]] = [
            (card_ids[a], card_ids[b])
            for a, b in zip(at["a_idx"].tolist(), at["b_idx"].tolist())
        ]
        rng.shuffle(at_pairs)
        if len(at_pairs) > ability_trigger_sample:
            at_pairs = at_pairs[:ability_trigger_sample]
        result["ability_trigger"] = at_pairs
        log.info("load_relation_pairs: %d ability_trigger pairs", len(at_pairs))

    # decomposed_candidates — commander → deck candidate (directed)
    dc = data.get("decomposed_candidates")
    if dc is not None:
        dc_pairs: list[tuple[str, str]] = [
            (card_ids[a], card_ids[b])
            for a, b in zip(dc["a_idx"].tolist(), dc["b_idx"].tolist())
        ]
        rng.shuffle(dc_pairs)
        if len(dc_pairs) > decomposed_sample:
            dc_pairs = dc_pairs[:decomposed_sample]
        result["decomposed_candidates"] = dc_pairs
        log.info("load_relation_pairs: %d decomposed_candidates pairs", len(dc_pairs))

    total = sum(len(v) for v in result.values())
    log.info(
        "load_relation_pairs: %d total pairs across %d relations",
        total, len(result),
    )
    return result


def train_bilinear_phase(
    encoder: CardEncoder,
    head: BilinearSynergyHead,
    pairs_by_relation: dict[str, list[tuple[str, str]]],
    embeddings: dict[str, np.ndarray],
    epochs: int,
    lr: float,
    batch_size: int = 256,
    temperature: float = 0.07,
    checkpoint_prefix: str = "phase",
) -> dict:
    """Phase 2 (Option B): learn W_r bilinear matrices with frozen encoder.

    For each epoch every relation type's pairs are processed in separate
    passes.  Within each pass, pairs are shuffled and split into mini-batches
    of size batch_size.  Loss: asymmetric bilinear InfoNCE where the diagonal
    of the score matrix is the positive pair and every off-diagonal entry in
    the same row is an in-batch negative.

    The encoder is frozen — its Phase 1 geometry is never touched.  All cards
    are pre-projected through the encoder once at the start of training and
    the cached projections are reused every epoch.

    Checkpoints:
        phase2_bilinear_best.pt  — saved whenever a new best loss is found
        phase2_bilinear_epochN.pt — saved at the end of training
    """
    device = next(encoder.parameters()).device

    # ── Freeze encoder; pre-project all cards once ────────────────────────────
    encoder.requires_grad_(False)
    encoder.eval()
    all_ids = list(embeddings.keys())
    id_to_idx = {cid: i for i, cid in enumerate(all_ids)}
    all_raw = torch.from_numpy(
        np.stack([embeddings[k] for k in all_ids]).astype(np.float32)
    ).to(device)
    log.info(
        "Phase 2 bilinear: pre-projecting %d cards through frozen encoder…",
        len(all_ids),
    )
    with torch.no_grad():
        all_proj = torch.cat(
            [encoder(all_raw[i: i + 512]) for i in range(0, all_raw.size(0), 512)],
            dim=0,
        )  # (N, D')
    log.info("Phase 2 bilinear: projections cached, shape=%s", tuple(all_proj.shape))

    # ── Build per-relation index arrays ───────────────────────────────────────
    rel_arrays: dict[str, np.ndarray] = {}
    for rel_name, pairs in pairs_by_relation.items():
        if rel_name not in head.rel_to_idx:
            log.warning(
                "Phase 2 bilinear: unknown relation '%s' — skipping", rel_name
            )
            continue
        idx_list: list[tuple[int, int]] = []
        n_skipped = 0
        for a_id, b_id in pairs:
            ai = id_to_idx.get(a_id)
            bi = id_to_idx.get(b_id)
            if ai is None or bi is None:
                n_skipped += 1
                continue
            idx_list.append((ai, bi))
        if n_skipped:
            log.warning(
                "Phase 2 bilinear: %s — %d pairs skipped (card not in embeddings)",
                rel_name, n_skipped,
            )
        if len(idx_list) >= batch_size:
            rel_arrays[rel_name] = np.array(idx_list, dtype=np.int64)
            log.info(
                "Phase 2 bilinear: %s → %d pairs", rel_name, len(idx_list)
            )
        else:
            log.warning(
                "Phase 2 bilinear: %s — only %d pairs (need >= batch_size=%d), skipping",
                rel_name, len(idx_list), batch_size,
            )

    if not rel_arrays:
        log.error(
            "Phase 2 bilinear: no relation has enough pairs — "
            "re-export artifact and ensure synergy stages have run."
        )
        return {
            "phase": "2_bilinear",
            "best_loss": None,
            "best_epoch": None,
            "final_epoch": 0,
            "stopped_early": False,
        }

    active_rels = list(rel_arrays.keys())
    log.info(
        "Phase 2 bilinear: training %d relations: %s  batch_size=%d  temp=%.3f",
        len(active_rels), active_rels, batch_size, temperature,
    )

    # ── Optimiser (head parameters only) ─────────────────────────────────────
    head = head.to(device)
    optimizer = torch.optim.AdamW(head.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)

    best_loss = float("inf")
    best_epoch = 0

    for epoch in range(epochs):
        head.train()
        total_loss = 0.0
        n_batches = 0

        for rel_name in active_rels:
            rel_idx = head.rel_to_idx[rel_name]
            arr = rel_arrays[rel_name]
            perm = np.random.permutation(len(arr))
            arr = arr[perm]

            for start in range(0, len(arr) - batch_size + 1, batch_size):
                batch = arr[start: start + batch_size]
                z_a = all_proj[batch[:, 0].tolist()]
                z_b = all_proj[batch[:, 1].tolist()]

                loss = bilinear_nt_xent_loss(z_a, z_b, head, rel_idx, temperature)
                optimizer.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(head.parameters(), 1.0)
                optimizer.step()
                total_loss += loss.item()
                n_batches += 1

        scheduler.step()
        avg = total_loss / max(n_batches, 1)
        log.info(
            "Phase 2 bilinear  epoch %d/%d  loss=%.4f  lr=%.2e",
            epoch + 1,
            epochs,
            avg,
            scheduler.get_last_lr()[0],
        )

        if WANDB_ENABLED:
            _wandb_log(
                {
                    "phase": "2_bilinear",
                    "epoch": epoch + 1,
                    "loss": avg,
                    "lr": scheduler.get_last_lr()[0],
                }
            )

        if avg < best_loss:
            best_loss = avg
            best_epoch = epoch + 1
            save_checkpoint(head, checkpoint_prefix + "2_bilinear_best")

    save_checkpoint(head, f"{checkpoint_prefix}2_bilinear_epoch{epochs}")
    return {
        "phase": "2_bilinear",
        "best_loss": best_loss,
        "best_epoch": best_epoch,
        "final_epoch": epochs,
        "stopped_early": False,
    }


# ── Phase 3 ───────────────────────────────────────────────────────────────────


def save_checkpoint(model: nn.Module, name: str):
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    path = CHECKPOINT_DIR / f"{name}.pt"
    torch.save(model.state_dict(), path)
    latest = CHECKPOINT_DIR / "latest.pt"
    if latest.exists() or latest.is_symlink():
        latest.unlink()
    shutil.copy2(path, latest)
    log.info("Checkpoint saved: %s", path)


def load_checkpoint(model: nn.Module, name: str, device: torch.device) -> nn.Module:
    path = CHECKPOINT_DIR / f"{name}.pt"
    if not path.exists():
        log.warning("Checkpoint not found: %s — starting from scratch", path)
        return model

    state = torch.load(path, map_location=device)
    model.load_state_dict(state, strict=False)
    log.info("Loaded checkpoint: %s", path)
    return model


# ── Artifact loaders (--dataset mode, no DB required) ─────────────────────────


def load_artifact(path: str) -> dict:
    """Load the training artifact produced by export_dataset*.py."""
    log.info("Loading training artifact: %s", path)
    data = torch.load(path, map_location="cpu", weights_only=False)
    meta = data.get("meta", {})
    log.info(
        "Artifact: %d cards, %d functional pairs, %d synergy pairs, "
        "%d decks, %d positions (created %s)",
        meta.get("card_count", 0),
        meta.get("functional_pair_count", 0),
        meta.get("synergy_count", 0),
        meta.get("deck_count", 0),
        meta.get("synergy_pos_count", 0),
        meta.get("created_at", "?")[:19],
    )
    log.info(
        "Artifact provenance: git=%s",
        meta.get("git_commit", "unknown")[:12],
    )
    sig = meta.get("signal_config")
    if sig:
        log.info("Phase 2 signal config: %s", sig)
    else:
        log.warning(
            "No signal_config in artifact — cannot verify Phase 2 signal composition "
            "(re-export to embed provenance)."
        )
    return data


def load_embeddings_from_artifact(data: dict) -> dict[str, np.ndarray]:
    """Reconstruct {card_id: np.ndarray} from artifact tensors."""
    card_ids = data["card_ids"]
    emb_matrix = data["embeddings"].numpy()
    return {cid: emb_matrix[i] for i, cid in enumerate(card_ids)}


def load_staple_pairs_from_artifact(
    data: dict,
) -> list[tuple[int, int, float]]:
    """Extract staple role pairs from artifact for Phase 1 training.

    Returns [(a_idx, b_idx, cmc_weight), …] where indices are into card_ids.
    Returns [] if the artifact was built before staple_pairs were added
    (re-export to include them).
    """
    sp = data.get("staple_pairs")
    if sp is None:
        log.info("load_staple_pairs_from_artifact: no staple_pairs key — re-export artifact to include")
        return []
    a = sp["a_idx"].tolist()
    b = sp["b_idx"].tolist()
    w = sp["weights"].tolist()
    log.info("load_staple_pairs_from_artifact: %d staple pairs loaded", len(a))
    return list(zip(a, b, w))


def _build_color_buckets_from_artifact(data: dict) -> dict[str, list[int]]:
    """Group card indices by color identity for ColorBucketBatchSampler.

    Returns {color_key: [idx, …]} where color_key is the sorted color letters
    joined as a string (e.g. "WU", "BRG") or "C" for colorless cards.
    """
    from collections import defaultdict as _defaultdict
    card_ids = data["card_ids"]
    card_meta = data.get("card_meta", {})
    buckets: dict[str, list[int]] = _defaultdict(list)
    for i, cid in enumerate(card_ids):
        meta = card_meta.get(cid, {})
        ci = meta.get("color_identity") or []
        if isinstance(ci, (list, tuple)):
            key = "".join(sorted(ci)) or "C"
        else:
            key = "C"
        buckets[key].append(i)
    log.info(
        "_build_color_buckets_from_artifact: %d color buckets (largest: %d cards)",
        len(buckets),
        max(len(v) for v in buckets.values()) if buckets else 0,
    )
    return dict(buckets)


def load_synergy_pairs_from_artifact(
    data: dict,
    sample: int = 500_000,
    max_consumers_per_producer: int = 20,
    max_producer_fanout: int = 100,
    effect_peer_sample: int = 200_000,
) -> list[tuple]:
    """Build positive pairs for Phase 2 NT-Xent from artifact synergy data.

    Two sources are combined:

    1. Consumer-peer pairs (from synergy["a_idx/b_idx/labels"]):
       Groups positive-synergy consumers by their shared producer, then creates
       (consumer_A, consumer_B) pairs.  Producers with raw fanout above
       max_producer_fanout are skipped — high fanout indicates a generic trigger
       (e.g. creature_etb, death_trigger) whose consumers span many unrelated
       roles, corrupting NT-Xent training.  Narrow producers (enchantment_cast
       payoffs, spell-cast subtypes) have fanout well below the threshold and
       contribute clean same-role peer pairs.

    2. Effect-peer pairs (from effect_peer["a_idx/b_idx"] if present):
       Cards sharing (trigger_event, effect_class) from compute_effect_peer_synergy.
       Used directly as positive pairs — no producer-grouping needed.  This
       covers the ETB/death space that the fanout filter excludes from source 1:
         etb/draw   → Cloudkin Seer, Mulldrifter, Wall of Blossoms
         etb/recursion → Eternal Witness, Archaeomancer
         death/damage  → Impact Tremors, Purphoros

    max_producer_fanout: skip producers with more raw consumers than this.
        Default 100 cuts creature_etb/death_trigger (fanout in the hundreds)
        while keeping SpellCast subtypes and draw/lifegain triggers (fanout ~20–60).
    max_consumers_per_producer: cap before C(n,2) for surviving producers.
    sample: cap on consumer-peer pairs (shuffled before truncation).
    effect_peer_sample: cap on effect-peer pairs (shuffled before truncation).
    """
    from collections import defaultdict

    card_ids = data["card_ids"]
    syn = data["synergy"]
    a_list = syn["a_idx"].tolist()
    b_list = syn["b_idx"].tolist()
    l_list = syn["labels"].tolist()

    # Group consumers by producer (positive synergy only); record raw fanout
    # before sampling so max_producer_fanout can filter generic triggers.
    producer_to_consumers: defaultdict[int, list[int]] = defaultdict(list)
    for a, b, l in zip(a_list, b_list, l_list):
        if l > 0.5:
            producer_to_consumers[a].append(b)

    rng = random.Random(42)
    pairs: list[tuple] = []
    n_producers_used = 0
    n_producers_skipped = 0

    for consumers in producer_to_consumers.values():
        if len(consumers) < 2:
            continue
        if len(consumers) > max_producer_fanout:
            n_producers_skipped += 1
            continue
        n_producers_used += 1
        if len(consumers) > max_consumers_per_producer:
            consumers = rng.sample(consumers, max_consumers_per_producer)
        for i in range(len(consumers)):
            for j in range(i + 1, len(consumers)):
                pairs.append((card_ids[consumers[i]], card_ids[consumers[j]], 1.0))

    rng.shuffle(pairs)
    if len(pairs) > sample:
        pairs = pairs[:sample]

    log.info(
        "load_synergy_pairs_from_artifact: %d consumer-peer pairs "
        "(%d producers used, %d skipped for fanout>%d, sample cap=%d)",
        len(pairs), n_producers_used, n_producers_skipped, max_producer_fanout, sample,
    )

    # Effect-peer pairs — direct positives, no producer-grouping needed.
    ep = data.get("effect_peer")
    if ep is not None:
        ep_a = ep["a_idx"].tolist()
        ep_b = ep["b_idx"].tolist()
        ep_pairs = [(card_ids[a], card_ids[b], 1.0) for a, b in zip(ep_a, ep_b)]
        rng.shuffle(ep_pairs)
        if len(ep_pairs) > effect_peer_sample:
            ep_pairs = ep_pairs[:effect_peer_sample]
        log.info("load_synergy_pairs_from_artifact: + %d effect_peer pairs", len(ep_pairs))
        pairs = pairs + ep_pairs
    else:
        log.info("load_synergy_pairs_from_artifact: no effect_peer key in artifact (re-export to include)")

    return pairs


# ── Entry point ───────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=str,
        default="",
        help="Path to pre-built training artifact (.pt from export_dataset.py). "
        "When set, all DB queries are skipped — no DATABASE_URL required.",
    )
    parser.add_argument("--phase", type=int, choices=[1, 2], default=2)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument(
        "--neg-ratio",
        type=int,
        default=3,
        help="Negative pairs per positive for phase 2",
    )
    parser.add_argument(
        "--hard-neg-frac",
        type=float,
        default=0.5,
        help="Fraction of negatives that are hard (nearest-neighbour) vs random",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=500_000,
        help="Max positive pairs to sample from synergy_edges",
    )
    parser.add_argument(
        "--combo-sample",
        type=int,
        default=200_000,
        help="Max combo_package card pairs to include as hard positives "
        "(0 to disable; requires import_spellbook.py to have run)",
    )
    parser.add_argument(
        "--commander-value-sample",
        type=int,
        default=200_000,
        help="Max commander_value edges to include as soft-label positives "
        "(0 to disable; uses stored score 1.0/0.8/0.6 as label)",
    )
    parser.add_argument(
        "--label-smoothing",
        type=float,
        default=0.1,
        help="Label smoothing epsilon (0=off); pos→1-ε/2, neg→ε/2",
    )
    parser.add_argument(
        "--staple-pair-weight",
        type=float,
        default=0.5,
        dest="staple_pair_weight",
        help="Phase 1: loss weight applied to the staple role-pair NT-Xent term "
        "(0=disabled; default 0.5 — half the weight of the noise-augmentation term). "
        "Per-pair CMC weights further scale this: same-CMC pairs → full weight, "
        "3+-CMC-apart pairs → 0.5× weight.",
    )
    parser.add_argument(
        "--noise",
        type=float,
        default=0.05,
        help="Phase 1: std of Gaussian noise added to create augmented views",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.07,
        help="Phase 1: NT-Xent temperature (lower=sharper contrast)",
    )
    parser.add_argument(
        "--temp-start",
        type=float,
        default=0.5,
        help="Phase 2: initial NT-Xent temperature (high=soft gradients). "
        "Phase 2 default via run.ps1: 0.3.",
    )
    parser.add_argument(
        "--temp-end",
        type=float,
        default=0.05,
        help="Phase 2: final NT-Xent temperature (low=sharp clusters). "
        "Phase 2 default via run.ps1: 0.07.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from latest checkpoint (phase2→phase2_best, "
        "phase1→phase1_best; falls back to previous phase if not found)",
    )
    parser.add_argument(
        "--encoder-lr-scale",
        type=float,
        default=0.1,
        help="Phase 2: lr scale factor for encoder parameters (default 0.1 — "
        "encoder drifts 10× slower to protect Phase 1 geometry).",
    )
    parser.add_argument(
        "--bilinear",
        action="store_true",
        help="Phase 2 (Option B): train BilinearSynergyHead W_r matrices with "
        "frozen encoder instead of NT-Xent on the full card encoder.  Requires "
        "--dataset (artifact mode).  Saves phase2_bilinear_best.pt; does NOT "
        "update phase2_best.pt (which still holds the Phase 1 encoder for Phase 3).",
    )
    parser.add_argument(
        "--bilinear-temperature",
        type=float,
        default=0.07,
        dest="bilinear_temperature",
        help="Phase 2 bilinear: InfoNCE temperature (fixed, no annealing). "
        "Default 0.07 matches Phase 1 final temperature.",
    )
    args = parser.parse_args()

    pfx = "phase"

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    log.info("Training phase %d on %s", args.phase, device)

    if WANDB_ENABLED:
        wandb.init(
            project=os.environ.get("WANDB_PROJECT", "edh-builder"),
            config=vars(args),
        )

    # Artifact mode: load all data from a pre-built .pt file instead of DB.
    _artifact: dict | None = None
    if args.dataset:
        _artifact = load_artifact(args.dataset)

    if args.phase == 1:
        embeddings = (
            load_embeddings_from_artifact(_artifact) if _artifact else load_embeddings()
        )
        if not embeddings:
            log.error("No embeddings found -- run the ingest pipeline first.")
            return

        input_dim = len(next(iter(embeddings.values())))
        model = CardEncoder(input_dim=input_dim).to(device)
        if args.resume:
            load_checkpoint(model, pfx + "1_best", device)

        dataset = AllCardsDataset(embeddings)
        log.info("Dataset: %d cards", len(dataset))

        staple_pairs = load_staple_pairs_from_artifact(_artifact) if _artifact else []
        color_buckets = _build_color_buckets_from_artifact(_artifact) if _artifact else None

        summary = train_contrastive_phase(
            model,
            dataset,
            args.epochs,
            args.lr,
            args.batch_size,
            noise_std=args.noise,
            temperature=args.temperature,
            checkpoint_prefix=pfx,
            staple_pairs=staple_pairs or None,
            staple_pair_weight=args.staple_pair_weight,
            color_buckets=color_buckets,
        )
        _wandb_summary(summary)

    elif args.phase == 2:
        embeddings = (
            load_embeddings_from_artifact(_artifact) if _artifact else load_embeddings()
        )
        if not embeddings:
            log.error("No embeddings found -- run the ingest pipeline first.")
            return

        if args.bilinear:
            # ── Option B: bilinear relational scoring ─────────────────────────
            if not _artifact:
                log.error(
                    "Phase 2 --bilinear requires --dataset pointing to mtg_dataset.pt"
                )
                return

            pairs_by_relation = load_relation_pairs_from_artifact(
                _artifact,
                combo_sample=args.combo_sample,
                effect_peer_sample=args.sample,
                ability_trigger_sample=args.sample,
                decomposed_sample=args.sample,
            )
            if not any(pairs_by_relation.values()):
                log.error(
                    "Phase 2 bilinear: no relation pairs found in artifact — "
                    "re-export with export_dataset stage."
                )
                return

            input_dim = len(next(iter(embeddings.values())))
            encoder = CardEncoder(input_dim=input_dim).to(device)
            # Always warm-start encoder from Phase 1; it is frozen during bilinear.
            warm = "phase1_best"
            if (CHECKPOINT_DIR / (warm + ".pt")).exists():
                load_checkpoint(encoder, warm, device)
            else:
                log.error(
                    "Phase 2 bilinear requires phase1_best checkpoint — run Phase 1 first."
                )
                return

            embed_dim = encoder.net[-1].out_features
            head = BilinearSynergyHead(embed_dim=embed_dim).to(device)
            if args.resume:
                bilinear_ckpt = pfx + "2_bilinear_best"
                if (CHECKPOINT_DIR / (bilinear_ckpt + ".pt")).exists():
                    load_checkpoint(head, bilinear_ckpt, device)
                    log.info("Resumed bilinear head from %s", bilinear_ckpt)
                else:
                    log.warning("--resume set but %s not found — starting fresh", bilinear_ckpt)

            summary = train_bilinear_phase(
                encoder,
                head,
                pairs_by_relation,
                embeddings,
                args.epochs,
                args.lr,
                args.batch_size,
                temperature=args.bilinear_temperature,
                checkpoint_prefix=pfx,
            )
            _wandb_summary(summary)

        else:
            # ── Option A / original NT-Xent path ─────────────────────────────
            if _artifact:
                pairs = load_synergy_pairs_from_artifact(_artifact, sample=args.sample)
            else:
                pairs = load_synergy_pairs(
                    embeddings,
                    neg_ratio=args.neg_ratio,
                    sample=args.sample,
                    hard_neg_frac=args.hard_neg_frac,
                    combo_sample=args.combo_sample,
                    commander_value_sample=args.commander_value_sample,
                )
            if not pairs:
                log.error("No synergy pairs found -- run compute_synergy stage first.")
                return

            dataset = SynergyDataset(pairs, embeddings)
            log.info("Dataset: %d pairs", len(dataset))

            input_dim = len(next(iter(embeddings.values())))
            model = CardEncoder(input_dim=input_dim).to(device)
            if args.resume:
                warm = "phase1_best"
                if (CHECKPOINT_DIR / (warm + ".pt")).exists():
                    load_checkpoint(model, warm, device)
                else:
                    fallback = pfx + "1_best"
                    log.info("No %s found -- loading %s as warm start", warm, fallback)
                    load_checkpoint(model, fallback, device)

            staple_pairs_p2 = load_staple_pairs_from_artifact(_artifact) if _artifact else []

            # Build index-addressable embedding array for staple pair lookups.
            # Staple indices are offsets into artifact card_ids; SynergyDataset
            # uses a card_id dict so we need a parallel numpy array.
            staple_embs_p2: np.ndarray | None = None
            if staple_pairs_p2 and _artifact:
                card_ids_list = _artifact["card_ids"]
                staple_embs_p2 = np.stack(
                    [embeddings[cid] for cid in card_ids_list]
                ).astype(np.float32)

            summary = train_synergy_phase(
                model,
                dataset,
                args.epochs,
                args.lr,
                args.batch_size,
                temp_start=args.temp_start,
                temp_end=args.temp_end,
                encoder_lr_scale=args.encoder_lr_scale,
                checkpoint_prefix=pfx,
                staple_pairs=staple_pairs_p2 or None,
                staple_pair_weight=args.staple_pair_weight,
                staple_embs=staple_embs_p2,
            )
            _wandb_summary(summary)

# (Phase 3 CommanderScorer training removed in #151 — composition-first.)

    else:
        log.warning("Phase %d not yet implemented.", args.phase)

    if WANDB_ENABLED:
        wandb.finish()


if __name__ == "__main__":
    main()
