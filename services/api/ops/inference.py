"""Inference helpers for the DeckConstructor model.

Manages lazy-loaded singletons for the model and card embeddings, plus
core inference functions (scoring, recall evaluation).  All DB access
here uses synchronous psycopg2 — these functions are intended to be run
in a thread executor from async FastAPI handlers.
"""

from __future__ import annotations

import logging
import os
import random
import time
from pathlib import Path
from typing import Optional

import numpy as np
import psycopg2
import psycopg2.extras
import torch

from ops.model import CardEncoder, DeckConstructor

log = logging.getLogger(__name__)

CHECKPOINT_DIR = Path(os.environ.get("MODEL_CHECKPOINT_DIR", "/app/checkpoints"))

# ── Module-level caches ───────────────────────────────────────────────────────

_model_cache: dict[str, DeckConstructor] = {}
_embeddings_cache: dict[str, dict[str, np.ndarray]] = {}   # keyed by db_url
_recall_cache: dict[str, tuple[float, dict]] = {}           # keyed by checkpoint name


def _sync_db_url(db_url: str) -> str:
    """Convert asyncpg URL format to psycopg2-compatible URL."""
    return db_url.replace("postgresql+asyncpg://", "postgresql://")


def _get_conn(db_url: str):
    return psycopg2.connect(_sync_db_url(db_url))


# ── Model loading ─────────────────────────────────────────────────────────────

def get_model(checkpoint_name: str = "phase4_best") -> Optional[DeckConstructor]:
    """Lazy-load and cache a DeckConstructor by checkpoint name.

    Falls back to warm-starting the card_encoder from phase3_best if the
    requested checkpoint is not found.  Returns None if no checkpoint is
    available at all.
    """
    if checkpoint_name in _model_cache:
        return _model_cache[checkpoint_name]

    device = torch.device("cpu")
    model = DeckConstructor()
    model.eval()

    ckpt_path = CHECKPOINT_DIR / f"{checkpoint_name}.pt"
    if ckpt_path.exists():
        log.info("Loading checkpoint: %s", ckpt_path)
        state = torch.load(ckpt_path, map_location=device)
        model.load_state_dict(state)
        _model_cache[checkpoint_name] = model
        return model

    # phase4_best not found — try warm-starting encoder from phase3_best
    log.warning("Checkpoint %s not found, trying phase3_best for card_encoder", ckpt_path)
    phase3_path = CHECKPOINT_DIR / "phase3_best.pt"
    if phase3_path.exists():
        log.info("Warm-starting card_encoder from phase3_best")
        state = torch.load(phase3_path, map_location=device)
        # State dict may be a full DeckConstructor or a bare CardEncoder
        model_keys = set(model.card_encoder.state_dict().keys())
        ckpt_keys = set(state.keys())
        # If keys match card_encoder directly
        if model_keys.issubset(ckpt_keys):
            model.card_encoder.load_state_dict(
                {k: v for k, v in state.items() if k in model_keys}
            )
        else:
            # Keys prefixed with "card_encoder."
            prefix = "card_encoder."
            extracted = {k[len(prefix):]: v for k, v in state.items() if k.startswith(prefix)}
            if extracted:
                model.card_encoder.load_state_dict(extracted)
            else:
                # Bare CardEncoder checkpoint
                model.card_encoder.load_state_dict(state)
        _model_cache[checkpoint_name] = model
        return model

    log.error("No checkpoint available (tried %s and phase3_best)", checkpoint_name)
    return None


# ── Embeddings loading ────────────────────────────────────────────────────────

def get_embeddings(
    db_url: str,
    model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
) -> dict[str, np.ndarray]:
    """Lazy-load and cache all card embeddings from the DB.

    Returns {card_id (str): np.ndarray shape (384,)}.
    """
    cache_key = f"{db_url}::{model_name}"
    if cache_key in _embeddings_cache:
        return _embeddings_cache[cache_key]

    log.info("Loading embeddings from DB (model=%s)…", model_name)
    with _get_conn(db_url) as conn:
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

    embeddings: dict[str, np.ndarray] = {}
    for row in rows:
        vec = row["embedding"]
        if isinstance(vec, str):
            vec = np.fromstring(vec.strip("[]"), sep=",", dtype=np.float32)
        else:
            vec = np.array(vec, dtype=np.float32)
        embeddings[row["card_id"]] = vec

    log.info("Loaded %d embeddings", len(embeddings))
    _embeddings_cache[cache_key] = embeddings
    return embeddings


# ── Context seed from existing decklists ─────────────────────────────────────

def get_common_context(
    commander_id: str,
    db_url: str,
    min_freq: float = 0.3,
    max_cards: int = 20,
) -> list[str]:
    """Return card_ids that appear in >= min_freq of this commander's decks.

    Results are sorted by frequency descending, capped at max_cards.
    Returns an empty list if there are no decks for this commander.
    """
    with _get_conn(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ARRAY(SELECT unnest(card_ids)::text) AS card_ids
                FROM decks
                WHERE commander_id = %s::uuid
                """,
                (commander_id,),
            )
            rows = cur.fetchall()

    if not rows:
        return []

    n_decks = len(rows)
    freq: dict[str, int] = {}
    for (card_ids,) in rows:
        for cid in (card_ids or []):
            freq[cid] = freq.get(cid, 0) + 1

    threshold = min_freq * n_decks
    common = [
        (cid, count) for cid, count in freq.items()
        if count >= threshold and cid != commander_id
    ]
    common.sort(key=lambda x: x[1], reverse=True)
    return [cid for cid, _ in common[:max_cards]]


# ── Card scoring ──────────────────────────────────────────────────────────────

def score_cards(
    commander_id: str,
    context_ids: list[str],
    embeddings: dict[str, np.ndarray],
    model: DeckConstructor,
    all_ids: list[str],
    batch_size: int = 512,
) -> list[tuple[str, float]]:
    """Score all cards given commander + context, return sorted (card_id, score).

    Excludes the commander itself and any cards already in context_ids.
    If context is empty, uses the commander embedding as the single context token
    (the DeckConstructor requires at least 1 token in the sequence).
    """
    exclude = {commander_id} | set(context_ids)
    candidate_ids = [cid for cid in all_ids if cid not in exclude and cid in embeddings]

    if not candidate_ids:
        return []

    device = torch.device("cpu")
    model.eval()

    # Build commander and context tensors
    cmd_raw = torch.from_numpy(embeddings[commander_id]).unsqueeze(0)  # (1, 384)
    with torch.no_grad():
        z_cmd = model.card_encoder(cmd_raw)  # (1, 256)

    if context_ids:
        ctx_raw = torch.stack([
            torch.from_numpy(embeddings[cid])
            for cid in context_ids
            if cid in embeddings
        ])  # (T, 384)
        with torch.no_grad():
            z_ctx = model.card_encoder(ctx_raw).unsqueeze(0)  # (1, T, 256)
    else:
        # Fall back: use commander embedding as single context token
        z_ctx = z_cmd.unsqueeze(0)  # (1, 1, 256)

    # Pre-project all candidates in batches
    scores: list[tuple[str, float]] = []
    for start in range(0, len(candidate_ids), batch_size):
        batch_ids = candidate_ids[start: start + batch_size]
        cand_raw = torch.stack([
            torch.from_numpy(embeddings[cid]) for cid in batch_ids
        ])  # (C, 384)
        with torch.no_grad():
            z_cand = model.card_encoder(cand_raw).unsqueeze(0)  # (1, C, 256)
            # Expand commander and context to match batch
            batch_scores = model(
                z_cmd,   # (1, 256)
                z_ctx,   # (1, T, 256)
                z_cand,  # (1, C, 256)
            ).squeeze(0)  # (C,)
        for cid, sc in zip(batch_ids, batch_scores.tolist()):
            scores.append((cid, sc))

    scores.sort(key=lambda x: x[1], reverse=True)
    return scores


# ── Recall@K evaluation ───────────────────────────────────────────────────────

def recall_at_k(
    db_url: str,
    checkpoint_name: str = "phase4_best",
    ks: tuple[int, ...] = (1, 5, 10, 20, 50),
    n_decks: int = 50,
    positions_per_deck: int = 5,
    n_neg: int = 99,
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2",
) -> dict:
    """Compute Recall@K on a random sample of decks.

    Returns a dict with keys: recall_1, recall_5, recall_10, recall_20,
    recall_50, mrr, n_positions, random_baseline.

    Results are cached for 3600 seconds.
    """
    cache_key = checkpoint_name
    if cache_key in _recall_cache:
        ts, cached = _recall_cache[cache_key]
        if time.time() - ts < 3600:
            return cached

    model = get_model(checkpoint_name)
    if model is None:
        return {"error": "model unavailable", "checkpoint": checkpoint_name}

    embeddings = get_embeddings(db_url, embedding_model)
    if not embeddings:
        return {"error": "no embeddings", "checkpoint": checkpoint_name}

    all_ids = list(embeddings.keys())

    # Load decks
    with _get_conn(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT commander_id::text,
                       ARRAY(SELECT unnest(card_ids)::text) AS card_ids
                FROM decks
                WHERE commander_id IS NOT NULL
                """
            )
            rows = cur.fetchall()

    valid_decks = [
        {"commander_id": r[0], "card_ids": [c for c in (r[1] or []) if c in embeddings]}
        for r in rows
        if r[0] in embeddings
    ]
    valid_decks = [d for d in valid_decks if len(d["card_ids"]) >= 10]

    if not valid_decks:
        return {"error": "no valid decks for evaluation", "checkpoint": checkpoint_name}

    sampled = random.sample(valid_decks, min(n_decks, len(valid_decks)))

    hits: dict[int, int] = {k: 0 for k in ks}
    rr_sum = 0.0
    n_positions = 0

    model.eval()
    with torch.no_grad():
        for deck in sampled:
            cmd_id = deck["commander_id"]
            card_ids = deck["card_ids"]
            K = len(card_ids)
            if K < 2:
                continue

            n_pos = min(positions_per_deck, K - 1)
            positions = random.sample(range(1, K), n_pos)

            for pos in positions:
                target_id = card_ids[pos]
                context = card_ids[:pos]

                # Build candidate pool: target + n_neg random cards (excluding context/commander)
                exclude = {cmd_id, target_id} | set(context)
                pool = [cid for cid in all_ids if cid not in exclude]
                if len(pool) < n_neg:
                    continue
                negatives = random.sample(pool, n_neg)
                candidates = [target_id] + negatives

                # Score candidates
                cmd_raw = torch.from_numpy(embeddings[cmd_id]).unsqueeze(0)
                z_cmd = model.card_encoder(cmd_raw)  # (1, 256)

                if context:
                    ctx_raw = torch.stack([torch.from_numpy(embeddings[c]) for c in context])
                    z_ctx = model.card_encoder(ctx_raw).unsqueeze(0)  # (1, T, 256)
                else:
                    z_ctx = z_cmd.unsqueeze(0)  # (1, 1, 256)

                cand_raw = torch.stack([torch.from_numpy(embeddings[c]) for c in candidates])
                z_cand = model.card_encoder(cand_raw).unsqueeze(0)  # (1, C, 256)

                scores = model(z_cmd, z_ctx, z_cand).squeeze(0)  # (C,)
                # Rank: argsort descending
                ranked_indices = scores.argsort(descending=True).tolist()
                # target is at index 0 in candidates
                rank = ranked_indices.index(0) + 1  # 1-based rank

                rr_sum += 1.0 / rank
                n_positions += 1
                for k in ks:
                    if rank <= k:
                        hits[k] += 1

    if n_positions == 0:
        return {"error": "no positions evaluated", "checkpoint": checkpoint_name}

    result = {
        "checkpoint": checkpoint_name,
        "n_positions": n_positions,
        "mrr": round(rr_sum / n_positions, 4),
        "random_baseline": round(1.0 / (n_neg + 1), 4),
    }
    for k in ks:
        result[f"recall_{k}"] = round(hits[k] / n_positions, 4)

    _recall_cache[cache_key] = (time.time(), result)
    return result
