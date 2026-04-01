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
import hashlib
import json
import logging
import math
import os
import random
import shutil
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


# ── DB loading helpers (Phase 2 / 3 / 4) ──────────────────────────────────────


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


def load_decks(embeddings: dict[str, np.ndarray]) -> list[dict]:
    """Load human Commander decklists, filtered to embedded cards."""
    import json as _json

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("""
                SELECT commander_id::text,
                       ARRAY(SELECT unnest(card_ids)::text) AS card_ids,
                       metadata
                FROM decks
                WHERE commander_id IS NOT NULL
            """)
            rows = cur.fetchall()

    color_ids = load_color_identities(embeddings)
    all_ids   = list(embeddings.keys())

    _legal_cache: dict[frozenset, np.ndarray] = {}

    def _legal_indices(cmd_ci: frozenset) -> np.ndarray:
        if cmd_ci not in _legal_cache:
            idx = np.array(
                [i for i, cid in enumerate(all_ids)
                 if color_ids.get(cid, frozenset()) <= cmd_ci],
                dtype=np.int64,
            )
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
        cmd_ci   = color_ids.get(cmd_id, frozenset())
        metadata = row["metadata"] or {}
        if isinstance(metadata, str):
            metadata = _json.loads(metadata)
        for pid in metadata.get("partner_commander_ids", []):
            cmd_ci = cmd_ci | color_ids.get(pid, frozenset())
        decks.append({
            "commander_id":      cmd_id,
            "card_ids":          card_ids,
            "color_identity":    cmd_ci,
            "legal_neg_indices": _legal_indices(cmd_ci),
            "archetype":         metadata.get("archetype", "unknown"),
        })

    log.info("Loaded %d decks (%d skipped — commander or cards not embedded)",
             len(decks), len(rows) - len(decks))
    return decks


def load_synergy_positions(
    decks: list[dict],
    embeddings: dict[str, np.ndarray],
    combo_weight: float = 3.0,
    ability_weight: float = 2.0,
    tribal_weight: float = 1.5,
    synergy_limit_per_commander: int = 300,
) -> list[dict]:
    """Build Phase 4 synthetic training positions from combo packages and synergy edges."""
    from collections import Counter, defaultdict
    log.info(
        "Building synergy positions "
        "(combo=%.1f×, ability=%.1f×, tribal=%.1f×, limit=%d/commander)…",
        combo_weight, ability_weight, tribal_weight, synergy_limit_per_commander,
    )

    emb_set  = set(embeddings.keys())
    cmd_legal = {d["commander_id"]: d["legal_neg_indices"] for d in decks}
    commander_ids  = list(cmd_legal.keys())
    deck_card_sets = {d["commander_id"]: set(d["card_ids"]) for d in decks}

    positions: list[dict] = []

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT cpc.combo_package_id, cpc.card_id::text
                FROM combo_package_cards cpc
                JOIN combo_packages cp ON cp.id = cpc.combo_package_id
                WHERE cpc.card_id IS NOT NULL
                  AND cpc.is_template = FALSE
                  AND cp.legal_commander = TRUE
            """)
            pkg_map: dict[str, list[str]] = defaultdict(list)
            for pkg_id, card_id in cur.fetchall():
                if card_id in emb_set:
                    pkg_map[pkg_id].append(card_id)

        combo_count = 0
        for deck in decks:
            cmd_id  = deck["commander_id"]
            in_deck = deck_card_sets[cmd_id]
            legal   = cmd_legal[cmd_id]
            for pkg_card_list in pkg_map.values():
                overlap = [c for c in pkg_card_list if c in in_deck]
                if len(overlap) < 2:
                    continue
                for target in overlap:
                    positions.append({
                        "commander_id":      cmd_id,
                        "context_card_ids":  [c for c in overlap if c != target],
                        "target_card_id":    target,
                        "weight":            combo_weight,
                        "legal_neg_indices": legal,
                    })
                    combo_count += 1

        log.info("  %d combo completion positions", combo_count)

        with conn.cursor() as cur:
            cur.execute("""
                SELECT card_a::text, card_b::text, score_type
                FROM synergy_edges
                WHERE score_type IN ('ability_trigger', 'tribal_typeline')
                  AND card_a::text = ANY(%s)
            """, (commander_ids,))
            fwd = list(cur.fetchall())

            cur.execute("""
                SELECT card_b::text AS card_a, card_a::text AS card_b, score_type
                FROM synergy_edges
                WHERE score_type IN ('ability_trigger', 'tribal_typeline')
                  AND card_b::text = ANY(%s)
            """, (commander_ids,))
            rev = list(cur.fetchall())

        cmd_count: Counter = Counter()
        syn_count = 0
        for card_a, card_b, score_type in fwd + rev:
            if card_a not in cmd_legal or card_b not in emb_set:
                continue
            if cmd_count[card_a] >= synergy_limit_per_commander:
                continue
            weight = ability_weight if score_type == "ability_trigger" else tribal_weight
            positions.append({
                "commander_id":      card_a,
                "context_card_ids":  [],
                "target_card_id":    card_b,
                "weight":            weight,
                "legal_neg_indices": cmd_legal[card_a],
            })
            cmd_count[card_a] += 1
            syn_count += 1

        log.info("  %d ability/tribal positions across %d commanders",
                 syn_count, len(cmd_count))

    log.info("Total synergy positions: %d", len(positions))
    return positions


def load_synergy_positions_global(
    embeddings: dict[str, np.ndarray],
    ability_weight: float = 2.0,
    tribal_weight: float = 1.5,
    synergy_limit_per_commander: int = 300,
) -> list[dict]:
    """Build Phase 4 synergy positions for ALL legal commanders in synergy_edges."""
    from collections import Counter
    log.info("Building global synergy positions (all legal commanders, no decks required)…")

    emb_set = set(embeddings.keys())
    all_ids = list(embeddings.keys())
    color_ids = load_color_identities(embeddings)

    _legal_cache: dict[frozenset, np.ndarray] = {}

    def _legal_indices(cmd_ci: frozenset) -> np.ndarray:
        if cmd_ci not in _legal_cache:
            idx = np.array(
                [i for i, cid in enumerate(all_ids)
                 if color_ids.get(cid, frozenset()) <= cmd_ci],
                dtype=np.int64,
            )
            _legal_cache[cmd_ci] = idx if len(idx) > 0 else np.arange(len(all_ids), dtype=np.int64)
        return _legal_cache[cmd_ci]

    positions: list[dict] = []
    cmd_count: Counter = Counter()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT se.card_a::text, se.card_b::text, se.score_type
                FROM synergy_edges se
                JOIN cards c ON c.id = se.card_a
                WHERE se.score_type IN ('ability_trigger', 'tribal_typeline')
                  AND se.card_b IS NOT NULL
                  AND c.legalities->>'commander' = 'legal'
                  AND (c.type_line ILIKE '%Legendary Creature%'
                    OR c.type_line ILIKE '%Legendary Planeswalker%'
                    OR c.oracle_text ILIKE '%can be your commander%')
                ORDER BY se.card_a, se.score DESC
            """)
            rows = cur.fetchall()

    for card_a, card_b, score_type in rows:
        if card_a not in emb_set or card_b not in emb_set:
            continue
        if cmd_count[card_a] >= synergy_limit_per_commander:
            continue
        cmd_ci = color_ids.get(card_a, frozenset())
        weight = ability_weight if score_type == "ability_trigger" else tribal_weight
        positions.append({
            "commander_id":      card_a,
            "context_card_ids":  [],
            "target_card_id":    card_b,
            "weight":            weight,
            "legal_neg_indices": _legal_indices(cmd_ci),
        })
        cmd_count[card_a] += 1

    log.info("Global synergy positions: %d across %d commanders",
             len(positions), len(cmd_count))
    return positions


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


class CardEncoder(nn.Module):
    """Projects a pre-computed embedding into a shared latent space."""

    def __init__(
        self, input_dim: int = 768, hidden_dim: int = 512, output_dim: int = 256
    ):
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

    def __init__(
        self,
        input_dim: int = 768,
        embed_dim: int = 256,
        n_heads: int = 4,
        n_layers: int = 3,
    ):
        super().__init__()
        self.card_encoder = CardEncoder(input_dim=input_dim, output_dim=embed_dim)
        decoder_layer = nn.TransformerDecoderLayer(
            d_model=embed_dim,
            nhead=n_heads,
            dim_feedforward=embed_dim * 4,
            dropout=0.1,
            batch_first=True,
        )
        self.decoder = nn.TransformerDecoder(decoder_layer, num_layers=n_layers)
        self.scorer = nn.Linear(embed_dim, 1)
        # Learnable query token for synergy-only Phase 4 training.
        # Replaces the degenerate z_cmd-as-tgt pattern: instead of the decoder
        # attending to the commander from itself (tgt=memory=z_cmd), this token
        # is a free variable that learns "what does a commander need?" via
        # cross-attention to z_cmd as memory.  At inference, real deck cards
        # serve as tgt — training with query_token is compatible because both
        # cases use commander-as-memory.
        self.query_token = nn.Parameter(torch.zeros(1, 1, embed_dim))

    def forward(
        self,
        commander_emb: torch.Tensor,  # (B, D)
        deck_embs: torch.Tensor,  # (B, T, D)  partial deck so far
        candidate_embs: torch.Tensor,  # (B, C, D)  cards to score
    ) -> torch.Tensor:
        memory = commander_emb.unsqueeze(1)  # (B, 1, D)
        deck_ctx = self.decoder(deck_embs, memory)  # (B, T, D)
        ctx = deck_ctx.mean(dim=1, keepdim=True)  # (B, 1, D)
        return (ctx * candidate_embs).sum(dim=-1)  # (B, C)


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
        card_embs = torch.stack(
            [
                torch.from_numpy(self.embeddings[c])
                for c in deck["card_ids"]
                if c in self.embeddings
            ]
        )
        return cmd_emb, card_embs, deck["legal_neg_indices"]


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


# ── Phase 3 ───────────────────────────────────────────────────────────────────


def train_deck_phase(
    model: CardEncoder,
    dataset: DeckDataset,
    embeddings: dict[str, np.ndarray],
    epochs: int,
    lr: float,
    batch_size: int = 32,
    archetype_weight: dict[str, float] | None = None,
    checkpoint_prefix: str = "phase",
    encoder_lr_scale: float = 1.0,
    freeze_encoder: bool = False,
):
    """Phase 3: BPR ranking loss on human Commander decklists.

    For each (commander, deck_card, random_card) triple, push the commander
    embedding closer to actual deck cards than to random cards.

    BPR loss: -log(sigmoid(score_pos - score_neg))
    This teaches relative preference rather than absolute scores, which suits
    the noisy signal from human deckbuilding better than BCE.

    archetype_weight: optional dict mapping archetype label → loss multiplier
        (e.g. {"combo": 2.0, "tokens": 1.5}).  Decks whose archetype is not
        in the dict default to a weight of 1.0.  Pass None to disable.

    encoder_lr_scale: multiplier on lr for all encoder parameters.  Set below
        1.0 to protect Phase 2 geometry — the commanders artifact generates
        thousands of synthetic decks, giving the encoder far more gradient
        updates per epoch than training on human decklists alone.

    freeze_encoder: when True, skip training entirely and save the warm-started
        weights directly as the Phase 3 checkpoint.  Use this when the Phase 2
        encoder should be preserved verbatim — Phase 4 will warm-start from the
        saved checkpoint just as it would from a trained one.
    """
    device = next(model.parameters()).device

    if freeze_encoder:
        log.info(
            "Phase 3: freeze_encoder=True — skipping BPR training, "
            "saving Phase 2 weights as %s3_best",
            checkpoint_prefix,
        )
        save_checkpoint(model, checkpoint_prefix + "3_best")
        return {"phase": 3, "best_loss": None, "best_epoch": None,
                "final_epoch": 0, "stopped_early": False, "frozen": True}

    all_ids = list(embeddings.keys())
    all_embs = torch.from_numpy(np.stack([embeddings[k] for k in all_ids]))  # (N, D)

    effective_lr = lr * encoder_lr_scale
    if encoder_lr_scale < 1.0:
        log.info(
            "Phase 3: encoder_lr_scale=%.2f → effective lr=%.2e (base lr=%.2e)",
            encoder_lr_scale, effective_lr, lr,
        )
    optimizer = torch.optim.AdamW(model.parameters(), lr=effective_lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    all_embs = all_embs.to(device)

    best_loss = float("inf")
    best_epoch = 0
    for epoch in range(epochs):
        model.train()
        total_loss = 0.0
        n_batches = 0

        # Iterate directly over dataset (like Phase 4) so we can read per-deck
        # metadata (archetype) for optional loss weighting.
        deck_indices = list(range(len(dataset)))
        random.shuffle(deck_indices)

        for idx in deck_indices:
            cmd_emb, card_embs, legal_neg_idx = dataset[idx]
            cmd_emb = cmd_emb.to(device)  # (D,)
            card_embs = card_embs.to(device)  # (K, D)

            if card_embs.size(0) < 2:
                continue

            # Per-deck archetype loss weight
            deck_archetype = dataset.decks[idx].get("archetype", "unknown")
            weight = (
                archetype_weight.get(deck_archetype, 1.0) if archetype_weight else 1.0
            )

            # Project commander and deck cards
            z_cmd = model(cmd_emb.unsqueeze(0))  # (1, D')
            z_pos = model(card_embs)  # (K, D')

            # Sample K random negatives restricted to the commander's color identity
            K = card_embs.size(0)
            neg_pool = (
                legal_neg_idx.numpy()
                if hasattr(legal_neg_idx, "numpy")
                else legal_neg_idx
            )
            chosen = np.random.choice(neg_pool, size=K, replace=True)
            neg_idx = torch.from_numpy(chosen).to(device)
            z_neg = model(all_embs[neg_idx])  # (K, D')

            # BPR: cosine similarity between commander and pos/neg cards
            score_pos = (z_cmd * z_pos).sum(dim=-1)  # (K,)
            score_neg = (z_cmd * z_neg).sum(dim=-1)  # (K,)

            loss = -F.logsigmoid(score_pos - score_neg).mean() * weight

            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()

            total_loss += loss.item()
            n_batches += 1

        scheduler.step()
        avg = total_loss / max(n_batches, 1)
        log.info(
            "Phase 3  epoch %d/%d  loss=%.4f  lr=%.2e",
            epoch + 1,
            epochs,
            avg,
            scheduler.get_last_lr()[0],
        )

        if WANDB_ENABLED:
            _wandb_log(
                {
                    "phase": 3,
                    "epoch": epoch + 1,
                    "loss": avg,
                    "lr": scheduler.get_last_lr()[0],
                }
            )

        if avg < best_loss:
            best_loss = avg
            best_epoch = epoch + 1
            save_checkpoint(model, checkpoint_prefix + "3_best")

    save_checkpoint(model, f"{checkpoint_prefix}3_epoch{epochs}")
    return {"phase": 3, "best_loss": best_loss, "best_epoch": best_epoch,
            "final_epoch": epochs, "stopped_early": False, "frozen": False}


# ── Phase 4: Synergy-guided synthetic positions ───────────────────────────────


# ── Phase 4: Synergy-only training loop (Option A) ────────────────────────────


def train_synergy_positions_phase(
    model: "DeckConstructor",
    positions: list[dict],
    embeddings: dict[str, np.ndarray],
    epochs: int,
    lr: float,
    batch_size: int = 256,
    n_neg: int = 64,
    temp_start: float = 0.5,
    temp_end: float = 0.05,
    freeze_encoder: bool = True,
    encoder_lr_scale: float = 0.1,
    patience: int = 10,
    checkpoint_prefix: str = "phase",
):
    """Phase 4 (Option A): train the DeckConstructor purely on synergy edges.

    Replaces the deck-sequence autoregressive loop with a batched InfoNCE scorer:

        given (commander, candidate), rank known synergy partners above
        color-legal random negatives.

    No human deck sequences are used.  Training data scales with synergy_edges
    (tens of thousands of positions) rather than the number of imported decklists
    (~180), eliminating the memorisation problem while keeping the GPU saturated
    through large batches.

    The commander embedding serves as the sole context token — the decoder learns
    to produce a context vector that is similar to synergy partners and dissimilar
    to random cards.  This directly trains the scoring function that inference
    uses at deck-generation time.

    With freeze_encoder=True (default) the Phase 3 card representations are
    preserved; only the decoder and scorer are updated.  Collapse is impossible
    because the encoder is not a training variable.
    """
    if not positions:
        log.error("No synergy positions — cannot run synergy-only Phase 4.")
        return

    if freeze_encoder:
        model.card_encoder.requires_grad_(False)
        log.info("Phase 4 (synergy-only): card_encoder frozen — decoder + scorer only")
        trainable = [p for p in model.parameters() if p.requires_grad]
        optimizer = torch.optim.AdamW(trainable, lr=lr, weight_decay=1e-4)
    else:
        encoder_lr = lr * encoder_lr_scale
        log.info(
            "Phase 4 (synergy-only): encoder unfrozen — encoder lr=%.2e, decoder lr=%.2e",
            encoder_lr,
            lr,
        )
        optimizer = torch.optim.AdamW(
            [
                {"params": model.card_encoder.parameters(), "lr": encoder_lr},
                {
                    "params": list(model.decoder.parameters())
                    + list(model.scorer.parameters()),
                    "lr": lr,
                },
            ],
            weight_decay=1e-4,
        )

    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    device = next(model.parameters()).device

    all_ids = list(embeddings.keys())
    id_to_idx = {cid: i for i, cid in enumerate(all_ids)}
    all_raw = torch.from_numpy(
        np.stack([embeddings[k] for k in all_ids]).astype(np.float32)
    ).to(device)

    log.info(
        "Phase 4 (synergy-only): %d positions, batch_size=%d, %d epochs",
        len(positions),
        batch_size,
        epochs,
    )
    if patience > 0:
        log.info("Phase 4: early stopping patience=%d epochs", patience)

    best_loss = float("inf")
    best_epoch = 0
    no_improve = 0
    final_epoch = 0
    stopped_early = False

    for epoch in range(epochs):
        temperature = cosine_temperature(epoch, epochs, temp_start, temp_end)

        # Pre-project the full card pool for negative sampling (no grad).
        model.eval()
        with torch.no_grad():
            all_proj = torch.cat(
                [
                    model.card_encoder(all_raw[i : i + 512])
                    for i in range(0, all_raw.size(0), 512)
                ],
                dim=0,
            )
        model.train()

        epoch_positions = positions[:]
        random.shuffle(epoch_positions)

        total_loss = 0.0
        n_steps = 0

        for batch_start in range(0, len(epoch_positions), batch_size):
            batch = epoch_positions[batch_start : batch_start + batch_size]
            B = len(batch)

            cmd_indices = torch.tensor(
                [id_to_idx[p["commander_id"]] for p in batch],
                dtype=torch.long,
                device=device,
            )
            tgt_indices = torch.tensor(
                [id_to_idx[p["target_card_id"]] for p in batch],
                dtype=torch.long,
                device=device,
            )

            if freeze_encoder:
                # Encoder frozen: use pre-projected embeddings (no grad needed).
                z_cmd = all_proj[cmd_indices]  # (B, D)
                z_tgt = all_proj[tgt_indices]  # (B, D)
            else:
                # Encoder unfrozen: encode fresh to capture gradient.
                z_cmd = model.card_encoder(all_raw[cmd_indices])  # (B, D)
                z_tgt = model.card_encoder(all_raw[tgt_indices])  # (B, D)

            # Learnable query token as the decoder tgt: (B, 1, D).
            # The decoder cross-attends this token to the commander (memory),
            # learning "what does this commander need?" without the degenerate
            # tgt=memory=z_cmd pattern that produced near-random InfoNCE scores.
            z_ctx = model.query_token.expand(B, -1, -1)

            # Sample color-legal negatives from the pre-projected pool: (B, n_neg, D).
            neg_idx = np.vstack(
                [
                    np.random.choice(p["legal_neg_indices"], size=n_neg, replace=True)
                    for p in batch
                ]
            )
            z_neg = all_proj[torch.from_numpy(neg_idx).to(device)]  # (B, n_neg, D)

            # Candidates: target (pos 0) followed by negatives → (B, 1+n_neg, D).
            candidates = torch.cat([z_tgt.unsqueeze(1), z_neg], dim=1)

            scores = model(z_cmd, z_ctx, candidates)  # (B, 1+n_neg)

            weights = torch.tensor(
                [p["weight"] for p in batch],
                dtype=torch.float32,
                device=device,
            )
            per_pos_loss = -F.log_softmax(scores / temperature, dim=1)[:, 0]  # (B,)
            loss = (per_pos_loss * weights).mean()

            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()

            # Log unweighted mean so the curve stays comparable across weight configs.
            total_loss += per_pos_loss.mean().item()
            n_steps += 1

        scheduler.step()
        avg = total_loss / max(n_steps, 1)
        log.info(
            "Phase 4  epoch %d/%d  loss=%.4f  lr=%.2e  temp=%.4f",
            epoch + 1,
            epochs,
            avg,
            scheduler.get_last_lr()[0],
            temperature,
        )

        if WANDB_ENABLED:
            _wandb_log(
                {
                    "phase": 4,
                    "epoch": epoch + 1,
                    "loss": avg,
                    "lr": scheduler.get_last_lr()[0],
                    "temperature": temperature,
                }
            )

        final_epoch = epoch + 1
        if avg < best_loss:
            best_loss = avg
            best_epoch = epoch + 1
            no_improve = 0
            save_checkpoint(model, checkpoint_prefix + "4_best")
        else:
            no_improve += 1
            if patience > 0 and no_improve >= patience:
                stopped_early = True
                log.info(
                    "Phase 4: early stopping at epoch %d/%d "
                    "(no improvement for %d consecutive epochs, best=%.4f)",
                    final_epoch,
                    epochs,
                    patience,
                    best_loss,
                )
                break

    save_checkpoint(model, f"{checkpoint_prefix}4_epoch{final_epoch}")
    return {"phase": 4, "best_loss": best_loss, "best_epoch": best_epoch,
            "final_epoch": final_epoch, "stopped_early": stopped_early}


# ── Phase 4: Autoregressive deck construction (legacy) ────────────────────────


def train_deck_constructor_phase(
    model: DeckConstructor,
    dataset: DeckDataset,
    embeddings: dict[str, np.ndarray],
    epochs: int,
    lr: float,
    synergy_positions: list[dict] | None = None,
    syn_per_epoch: int = 1000,
    n_neg: int = 64,
    positions_per_deck: int = 10,
    temp_start: float = 0.5,
    temp_end: float = 0.05,
    freeze_encoder: bool = True,
    encoder_lr_scale: float = 0.1,
    patience: int = 10,
    checkpoint_prefix: str = "phase",
):
    """Phase 4: autoregressive deck construction via transformer decoder + InfoNCE.

    Each epoch interleaves two types of training steps:

    Deck steps (from DeckDataset):
        The model sees [commander, cards[0:K]] and must rank cards[K] above
        n_neg random color-legal cards.  Teaches sequence-level deck construction
        from human examples.

    Synergy steps (from synergy_positions, optional):
        The model sees [commander, (optional combo context)] and must rank a known
        synergy partner above n_neg negatives.  These positions are derived purely
        from oracle-text analysis (synergy_edges + combo_package_cards), so they
        generalise to brand-new commanders — a commander the model has never seen
        will have an embedding in the neighbourhood of commanders it was trained on,
        and those neighbourhood relationships carry the synergy signal forward.

    The weighted InfoNCE loss for synergy steps uses the position's `weight` field
    to scale gradient magnitude relative to deck steps (weight=1.0 for each deck
    position).  Combo completions (weight=3.0) dominate; ability/tribal (1.5–2.0)
    supplement; human decks provide context and balance.

    Temperature is cosine-annealed from temp_start → temp_end.
    When freeze_encoder=False the encoder trains at lr * encoder_lr_scale (default
    0.1×) to prevent collapsing Phase 3 representations.

    patience controls early stopping: training halts if the loss does not improve
    for this many consecutive epochs (0 = disabled).  Use this as a safety net
    when the encoder is unfrozen — the collapse pattern shows rapid loss increase
    after the minimum, so a patience of ~5–10 catches it before it matters.
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
            encoder_lr,
            encoder_lr_scale * 100,
            lr,
        )
        optimizer = torch.optim.AdamW(
            [
                {"params": model.card_encoder.parameters(), "lr": encoder_lr},
                {
                    "params": list(model.decoder.parameters())
                    + list(model.scorer.parameters()),
                    "lr": lr,
                },
            ],
            weight_decay=1e-4,
        )

    all_ids = list(embeddings.keys())
    all_raw = torch.from_numpy(
        np.stack([embeddings[k] for k in all_ids]).astype(np.float32)
    )
    # Pre-build raw tensors for synergy positions (looked up by card id)
    id_to_idx = {card_id: i for i, card_id in enumerate(all_ids)}

    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    device = next(model.parameters()).device
    all_raw = all_raw.to(device)

    n_syn_total = len(synergy_positions) if synergy_positions else 0
    n_syn_epoch = min(n_syn_total, syn_per_epoch) if synergy_positions else 0
    log.info(
        "Phase 4: %d deck sequences + %d/%d synergy positions per epoch",
        len(dataset),
        n_syn_epoch,
        n_syn_total,
    )
    if patience > 0:
        log.info("Phase 4: early stopping patience=%d epochs", patience)

    best_loss = float("inf")
    best_epoch = 0
    no_improve = 0
    final_epoch = 0
    stopped_early = False
    for epoch in range(epochs):
        temperature = cosine_temperature(epoch, epochs, temp_start, temp_end)

        # ── Pre-project the full card pool for fast negative sampling ──────────
        model.eval()
        with torch.no_grad():
            all_proj = torch.cat(
                [
                    model.card_encoder(all_raw[i : i + 512])
                    for i in range(0, all_raw.size(0), 512)
                ],
                dim=0,
            )  # (N, D), L2-normalised, no grad
        model.train()

        total_loss = 0.0
        n_steps = 0

        # ── Build unified shuffle of deck + synergy steps ─────────────────────
        # Sample a fresh random subset of synergy positions each epoch so the
        # model sees different positions over time while keeping epoch length
        # manageable.  Over many epochs the full synergy pool is covered.
        syn_sample = (
            random.sample(range(n_syn_total), n_syn_epoch) if synergy_positions else []
        )
        step_list: list[tuple[str, int]] = [
            ("deck", i) for i in range(len(dataset))
        ] + [("syn", i) for i in syn_sample]
        random.shuffle(step_list)

        for step_type, step_idx in step_list:
            # ── Deck step ─────────────────────────────────────────────────────
            if step_type == "deck":
                cmd_raw, card_raw, legal_neg_idx = dataset[step_idx]
                card_raw = card_raw.to(device)
                cmd_raw = cmd_raw.to(device)
                K = card_raw.size(0)
                if K < 2:
                    continue

                n_pos = min(positions_per_deck, K - 1)
                pos_list = random.sample(range(1, K), n_pos)
                legal_pool = (
                    legal_neg_idx
                    if isinstance(legal_neg_idx, np.ndarray)
                    else legal_neg_idx.numpy()
                )

                deck_loss = torch.tensor(0.0, device=device)
                for pos in pos_list:
                    z_cmd = model.card_encoder(cmd_raw.unsqueeze(0))  # (1, D)
                    z_context = model.card_encoder(card_raw[:pos])  # (pos, D)
                    z_target = model.card_encoder(card_raw[pos].unsqueeze(0))  # (1, D)

                    chosen = np.random.choice(legal_pool, size=n_neg, replace=True)
                    z_neg = all_proj[torch.from_numpy(chosen).to(device)].detach()

                    candidates = torch.cat([z_target, z_neg], dim=0)  # (1+n_neg, D)
                    scores = model(
                        z_cmd,
                        z_context.unsqueeze(0),
                        candidates.unsqueeze(0),
                    ).squeeze(0)
                    deck_loss = deck_loss + (
                        -F.log_softmax(scores / temperature, dim=0)[0]
                    )

                step_loss = deck_loss / n_pos

            # ── Synergy step ──────────────────────────────────────────────────
            else:
                sp = synergy_positions[step_idx]
                cmd_id = sp["commander_id"]
                target_id = sp["target_card_id"]
                if cmd_id not in id_to_idx or target_id not in id_to_idx:
                    continue

                cmd_raw_t = all_raw[id_to_idx[cmd_id]]
                target_raw_t = all_raw[id_to_idx[target_id]]
                legal_pool = sp["legal_neg_indices"]

                z_cmd = model.card_encoder(cmd_raw_t.unsqueeze(0))  # (1, D)
                z_target = model.card_encoder(target_raw_t.unsqueeze(0))  # (1, D)

                # Context: combo siblings projected live; empty context uses
                # commander as the seed token so the decoder has something to attend to.
                ctx_ids = sp["context_card_ids"]
                if ctx_ids:
                    ctx_raw = torch.stack(
                        [all_raw[id_to_idx[c]] for c in ctx_ids if c in id_to_idx]
                    )  # (C, D)
                    z_context = model.card_encoder(ctx_raw)  # (C, D)
                else:
                    z_context = z_cmd.detach()  # (1, D) — commander as seed

                chosen = np.random.choice(legal_pool, size=n_neg, replace=True)
                z_neg = all_proj[torch.from_numpy(chosen).to(device)].detach()

                candidates = torch.cat([z_target, z_neg], dim=0)  # (1+n_neg, D)
                scores = model(
                    z_cmd,
                    z_context.unsqueeze(0),
                    candidates.unsqueeze(0),
                ).squeeze(0)

                raw_loss = -F.log_softmax(scores / temperature, dim=0)[0]
                step_loss = raw_loss * sp["weight"]

            optimizer.zero_grad()
            step_loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            # Log unweighted loss so the curve stays interpretable
            total_loss += (
                (step_loss / sp["weight"]).item()
                if step_type == "syn"
                else step_loss.item()
            )
            n_steps += 1

        scheduler.step()
        avg = total_loss / max(n_steps, 1)
        log.info(
            "Phase 4  epoch %d/%d  loss=%.4f  lr=%.2e  temp=%.4f",
            epoch + 1,
            epochs,
            avg,
            scheduler.get_last_lr()[0],
            temperature,
        )

        if WANDB_ENABLED:
            _wandb_log(
                {
                    "phase": 4,
                    "epoch": epoch + 1,
                    "loss": avg,
                    "lr": scheduler.get_last_lr()[0],
                    "temperature": temperature,
                }
            )

        final_epoch = epoch + 1
        if avg < best_loss:
            best_loss = avg
            best_epoch = epoch + 1
            no_improve = 0
            save_checkpoint(model, checkpoint_prefix + "4_best")
        else:
            no_improve += 1
            if patience > 0 and no_improve >= patience:
                stopped_early = True
                log.info(
                    "Phase 4: early stopping at epoch %d/%d "
                    "(no improvement for %d consecutive epochs, best=%.4f)",
                    final_epoch,
                    epochs,
                    patience,
                    best_loss,
                )
                break

    save_checkpoint(model, f"{checkpoint_prefix}4_epoch{final_epoch}")
    return {"phase": 4, "best_loss": best_loss, "best_epoch": best_epoch,
            "final_epoch": final_epoch, "stopped_early": stopped_early}


# ── Checkpoint helpers ────────────────────────────────────────────────────────


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

    # If we're loading a DeckConstructor checkpoint into a CardEncoder, extract
    # just the card_encoder sub-module weights (keys prefixed "card_encoder.").
    model_keys = set(model.state_dict().keys())
    if not model_keys.issubset(set(state.keys())):
        prefix = "card_encoder."
        extracted = {
            k[len(prefix) :]: v for k, v in state.items() if k.startswith(prefix)
        }
        if extracted and model_keys.issubset(set(extracted.keys())):
            log.info(
                "Extracting card_encoder weights from DeckConstructor checkpoint: %s",
                path,
            )
            state = extracted

    model.load_state_dict(state, strict=False)
    log.info("Loaded checkpoint: %s", path)
    return model


# ── Artifact loaders (--dataset mode, no DB required) ─────────────────────────


def load_artifact(path: str) -> dict:
    """Load the training artifact produced by export_dataset*.py."""
    log.info("Loading training artifact: %s", path)
    file_sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    data = torch.load(path, map_location="cpu", weights_only=False)
    meta = data.get("meta", {})
    stored_sha = meta.get("sha256", "(none)")
    if stored_sha != "(none)" and stored_sha != file_sha:
        log.warning(
            "SHA256 MISMATCH — stored=%s  file=%s  (artifact may be corrupted)",
            stored_sha, file_sha,
        )
    log.info(
        "Artifact: %d cards, %d functional pairs, %d synergy pairs, "
        "%d decks, %d positions (created %s)  sha256=%s",
        meta.get("card_count", 0),
        meta.get("functional_pair_count", 0),
        meta.get("synergy_count", 0),
        meta.get("deck_count", 0),
        meta.get("position_count", 0),
        meta.get("created_at", "?")[:19],
        file_sha[:16],
    )
    git_commit = meta.get("git_commit", None)
    if git_commit:
        log.info("Artifact git_commit: %s", git_commit[:12])
    else:
        log.warning("No git_commit in artifact — cannot verify which code version produced it.")
    sig = meta.get("signal_config")
    if sig:
        log.info("Phase 2 signal_config: %s", sig)
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


def load_decks_from_artifact(data: dict) -> list[dict]:
    """Reconstruct deck list (same schema as load_decks) from artifact."""
    card_ids = data["card_ids"]
    decks = []
    for d in data["decks"]:
        cmd_idx = d["commander_idx"]
        decks.append(
            {
                "commander_id": card_ids[cmd_idx],
                "card_ids": [card_ids[i] for i in d["card_idxs"]],
                "color_identity": frozenset(d.get("color_identity", [])),
                "legal_neg_indices": d["legal_neg_indices"].numpy(),
                "archetype": d.get("archetype", "unknown"),
            }
        )
    return decks


def load_synergy_positions_from_artifact(data: dict) -> list[dict]:
    """Reconstruct Phase 4 positions (same schema as load_synergy_positions)."""
    card_ids = data["card_ids"]
    positions = []
    for p in data.get("synergy_positions", []):
        positions.append(
            {
                "commander_id": card_ids[p["commander_idx"]],
                "context_card_ids": [card_ids[i] for i in p["context_card_idxs"]],
                "target_card_id": card_ids[p["target_card_idx"]],
                "weight": float(p["weight"]),
                "legal_neg_indices": p["legal_neg_indices"].numpy(),
            }
        )
    return positions


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
    parser.add_argument("--phase", type=int, choices=[1, 2, 3, 4], default=2)
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
        help="Phase 2/4: initial NT-Xent temperature (high=soft gradients). "
        "Phase 2 default via run.ps1: 0.3.  Phase 4 default: 0.5.",
    )
    parser.add_argument(
        "--temp-end",
        type=float,
        default=0.05,
        help="Phase 2/4: final NT-Xent temperature (low=sharp clusters). "
        "Phase 2 default via run.ps1: 0.07.  Phase 4 default: 0.05.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from latest checkpoint (phase2→phase2_best, "
        "phase1→phase1_best; falls back to previous phase if not found)",
    )
    parser.add_argument(
        "--archetype-weight",
        type=str,
        default="",
        dest="archetype_weight",
        help="Phase 3: comma-separated archetype=weight pairs to upweight "
        "certain deck types in BPR loss "
        "(e.g. 'combo=2.0,tokens=1.5').  Archetypes not listed "
        "default to 1.0.  Leave empty to treat all decks equally.",
    )
    parser.add_argument(
        "--freeze-encoder",
        action="store_true",
        default=True,
        dest="freeze_encoder",
        help="Phase 4: freeze card_encoder weights so the decoder learns "
        "to use fixed Phase 3 representations without collapsing them "
        "(default: True; use --no-freeze-encoder to disable)",
    )
    parser.add_argument(
        "--no-freeze-encoder", action="store_false", dest="freeze_encoder"
    )
    parser.add_argument(
        "--encoder-lr-scale",
        type=float,
        default=0.1,
        help="Phase 2: lr scale factor for all encoder parameters (default 0.1 — encoder "
        "drifts 10× slower to protect Phase 1 geometry). "
        "Phase 4 --no-freeze-encoder: encoder lr as fraction of decoder lr "
        "(same semantics, same default).",
    )
    parser.add_argument(
        "--patience",
        type=int,
        default=10,
        help="Phase 4: early-stopping patience in epochs — halt if loss does not "
        "improve for this many consecutive epochs (0 = disabled, default 10)",
    )
    parser.add_argument(
        "--syn-per-epoch",
        type=int,
        default=1000,
        help="Phase 4: max synergy positions sampled per epoch "
        "(default 1000; fresh random sample each epoch covers "
        "the full pool over many epochs)",
    )
    parser.add_argument(
        "--combo-weight",
        type=float,
        default=3.0,
        help="Phase 4: loss multiplier for combo-completion synergy positions "
        "(default 3.0 — combo pieces must be played together, high signal)",
    )
    parser.add_argument(
        "--ability-weight",
        type=float,
        default=2.0,
        help="Phase 4: loss multiplier for ability_trigger synergy positions "
        "(default 2.0 — oracle-text derived trigger/payoff relationships)",
    )
    parser.add_argument(
        "--tribal-weight",
        type=float,
        default=1.5,
        help="Phase 4: loss multiplier for tribal_typeline synergy positions "
        "(default 1.5 — type-based membership, slightly softer signal)",
    )
    parser.add_argument(
        "--synergy-limit",
        type=int,
        default=300,
        help="Phase 4: max synergy positions per commander "
        "(default 300; increase if commanders have sparse edge coverage)",
    )
    parser.add_argument(
        "--synergy-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Phase 4: train purely on synergy positions (Option A, default). "
        "Drops human deck sequences; scales to all legal commanders; "
        "eliminates deck-memorisation.  Pass --no-synergy-only to use "
        "the legacy interleaved deck+synergy loop.",
    )
    parser.add_argument(
        "--syn-batch-size",
        type=int,
        default=256,
        help="Phase 4 --synergy-only: positions per gradient step "
        "(default 256 — larger than deck batch=32 to saturate the GPU)",
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
        )
        _wandb_summary(summary)

    elif args.phase == 3:
        embeddings = (
            load_embeddings_from_artifact(_artifact) if _artifact else load_embeddings()
        )
        if not embeddings:
            log.error("No embeddings found -- run the ingest pipeline first.")
            return

        # Use pre-built decks from the artifact when available (the commanders
        # mtg_commanders.pt stores synergy-derived synthetic decks in the same
        # DeckDataset schema.  Fall through to load_decks() only when no
        # artifact was provided or the artifact has no 'decks' key.
        if _artifact and "decks" in _artifact:
            decks = load_decks_from_artifact(_artifact)
        else:
            decks = load_decks(embeddings)
        if not decks:
            log.error("No decks found -- run import_decklists.py first.")
            return

        dataset = DeckDataset(decks, embeddings)
        log.info("Dataset: %d decks", len(dataset))

        input_dim = len(next(iter(embeddings.values())))
        model = CardEncoder(input_dim=input_dim).to(device)
        if args.resume:
            warm = "phase2_best"
            if (CHECKPOINT_DIR / (warm + ".pt")).exists():
                load_checkpoint(model, warm, device)
            else:
                fallback = pfx + "2_best"
                log.info("No %s found -- loading %s as warm start", warm, fallback)
                load_checkpoint(model, fallback, device)

        archetype_weight: dict[str, float] | None = None
        if args.archetype_weight:
            archetype_weight = {}
            for part in args.archetype_weight.split(","):
                name, _, val = part.strip().partition("=")
                if name and val:
                    try:
                        archetype_weight[name.strip()] = float(val.strip())
                    except ValueError:
                        log.warning(
                            "Invalid --archetype-weight entry %r -- skipped", part
                        )
            if archetype_weight:
                log.info("Archetype weights: %s", archetype_weight)

        summary = train_deck_phase(
            model,
            dataset,
            embeddings,
            args.epochs,
            args.lr,
            args.batch_size,
            archetype_weight=archetype_weight,
            checkpoint_prefix=pfx,
            encoder_lr_scale=args.encoder_lr_scale,
            freeze_encoder=args.freeze_encoder,
        )
        _wandb_summary(summary)

    elif args.phase == 4:
        embeddings = (
            load_embeddings_from_artifact(_artifact) if _artifact else load_embeddings()
        )
        if not embeddings:
            log.error("No embeddings found -- run the ingest pipeline first.")
            return

        input_dim = len(next(iter(embeddings.values())))
        model = DeckConstructor(input_dim=input_dim).to(device)
        p4_best = pfx + "4_best"
        p3_best = pfx + "3_best"
        if args.resume and (CHECKPOINT_DIR / (p4_best + ".pt")).exists():
            load_checkpoint(model, p4_best, device)
        else:
            phase3_encoder = CardEncoder(input_dim=input_dim).to(device)
            if (CHECKPOINT_DIR / (p3_best + ".pt")).exists():
                load_checkpoint(phase3_encoder, p3_best, device)
                model.card_encoder.load_state_dict(phase3_encoder.state_dict())
                log.info("Warm-started card_encoder from %s", p3_best)
            else:
                log.warning("No %s found -- card_encoder starts from scratch", p3_best)

        if args.synergy_only:
            if _artifact:
                syn_positions = load_synergy_positions_from_artifact(_artifact)
                log.info(
                    "Synergy-only mode: %d positions from artifact", len(syn_positions)
                )
            else:
                syn_positions = []
            if not syn_positions:
                # commanders artifact has no synergy_positions — build from DB.
                log.info(
                    "Synergy-only: no positions in artifact — loading from DB "
                    "(requires DATABASE_URL)"
                )
                syn_positions = load_synergy_positions_global(
                    embeddings,
                    ability_weight=args.ability_weight,
                    tribal_weight=args.tribal_weight,
                    synergy_limit_per_commander=args.synergy_limit,
                )

            summary = train_synergy_positions_phase(
                model,
                syn_positions,
                embeddings,
                args.epochs,
                args.lr,
                batch_size=args.syn_batch_size,
                n_neg=64,
                temp_start=args.temp_start,
                temp_end=args.temp_end,
                freeze_encoder=args.freeze_encoder,
                encoder_lr_scale=args.encoder_lr_scale,
                patience=args.patience,
                checkpoint_prefix=pfx,
            )
            _wandb_summary(summary)
        else:
            decks = (
                load_decks_from_artifact(_artifact)
                if _artifact
                else load_decks(embeddings)
            )
            if not decks:
                log.error("No decks found -- run import_decklists.py first.")
                return

            dataset = DeckDataset(decks, embeddings)
            log.info("Dataset: %d decks", len(dataset))

            if _artifact:
                syn_positions = load_synergy_positions_from_artifact(_artifact)
            else:
                syn_positions = load_synergy_positions(
                    decks,
                    embeddings,
                    combo_weight=args.combo_weight,
                    ability_weight=args.ability_weight,
                    tribal_weight=args.tribal_weight,
                    synergy_limit_per_commander=args.synergy_limit,
                )

            summary = train_deck_constructor_phase(
                model,
                dataset,
                embeddings,
                args.epochs,
                args.lr,
                synergy_positions=syn_positions,
                syn_per_epoch=args.syn_per_epoch,
                n_neg=64,
                positions_per_deck=10,
                temp_start=args.temp_start,
                temp_end=args.temp_end,
                freeze_encoder=args.freeze_encoder,
                encoder_lr_scale=args.encoder_lr_scale,
                patience=args.patience,
                checkpoint_prefix=pfx,
            )
            _wandb_summary(summary)

    else:
        log.warning("Phase %d not yet implemented.", args.phase)

    if WANDB_ENABLED:
        wandb.finish()


if __name__ == "__main__":
    main()
