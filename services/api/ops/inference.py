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
_color_cache: dict[str, dict[str, frozenset]] = {}          # keyed by db_url
_type_line_cache: dict[str, dict[str, str]] = {}            # keyed by db_url
_cmc_cache: dict[str, dict[str, float]] = {}                # keyed by db_url
_ramp_cache: dict[str, tuple[frozenset, dict]] = {}         # keyed by db_url
_land_staple_cache: dict[str, dict[str, str]] = {}          # keyed by db_url
_legal_ids_cache: dict[str, frozenset[str]] = {}            # keyed by db_url
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
    model_name: str = "sentence-transformers/all-mpnet-base-v2",
) -> dict[str, np.ndarray]:
    """Lazy-load and cache all card embeddings from the DB.

    Returns {card_id (str): np.ndarray shape (768,)}.
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


# ── Color identity ────────────────────────────────────────────────────────────

def get_legal_ids(db_url: str) -> frozenset[str]:
    """Return the set of card IDs that are legal in Commander. Cached."""
    if db_url in _legal_ids_cache:
        return _legal_ids_cache[db_url]

    log.info("Loading commander-legal card IDs from DB…")
    with _get_conn(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id::text FROM cards WHERE legalities->>'commander' = 'legal'"
            )
            rows = cur.fetchall()

    result: frozenset[str] = frozenset(r[0] for r in rows)
    log.info("Loaded %d commander-legal card IDs", len(result))
    _legal_ids_cache[db_url] = result
    return result


def get_color_identities(db_url: str) -> dict[str, frozenset]:
    """Return {card_id: frozenset of color letters} for all cards. Cached."""
    if db_url in _color_cache:
        return _color_cache[db_url]

    log.info("Loading color identities from DB…")
    with _get_conn(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id::text, color_identity FROM cards")
            rows = cur.fetchall()

    result: dict[str, frozenset] = {}
    for card_id, ci in rows:
        result[card_id] = frozenset(ci or [])

    log.info("Loaded color identities for %d cards", len(result))
    _color_cache[db_url] = result
    return result


def get_type_lines(db_url: str) -> dict[str, str]:
    """Return {card_id: type_line} for all cards. Cached."""
    if db_url in _type_line_cache:
        return _type_line_cache[db_url]

    log.info("Loading type lines from DB…")
    with _get_conn(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id::text, type_line FROM cards")
            rows = cur.fetchall()

    result: dict[str, str] = {card_id: (type_line or "") for card_id, type_line in rows}
    log.info("Loaded type lines for %d cards", len(result))
    _type_line_cache[db_url] = result
    return result


def get_cmc_map(db_url: str) -> dict[str, float]:
    """Return {card_id: cmc} for all cards. Cached."""
    if db_url in _cmc_cache:
        return _cmc_cache[db_url]

    log.info("Loading CMC values from DB…")
    with _get_conn(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id::text, cmc FROM cards")
            rows = cur.fetchall()

    result: dict[str, float] = {card_id: float(cmc or 0) for card_id, cmc in rows}
    log.info("Loaded CMC for %d cards", len(result))
    _cmc_cache[db_url] = result
    return result


def get_ramp_info(db_url: str) -> tuple[frozenset, dict[str, str]]:
    """Return (ramp_ids, guaranteed) for non-land mana-producing cards.

    ramp_ids: frozenset of card_ids whose oracle text contains '{T}: Add'
              (permanent mana sources — rocks, dorks, mana-producing lands
              are excluded via the type_line filter).
    guaranteed: {name: card_id} for Sol Ring and Arcane Signet specifically,
                used to force-include them regardless of model score.
    """
    if db_url in _ramp_cache:
        return _ramp_cache[db_url]

    log.info("Loading ramp card IDs from DB…")
    with _get_conn(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, name
                FROM cards
                WHERE type_line NOT ILIKE '%Land%'
                  AND legalities->>'commander' = 'legal'
                  AND (
                    -- Permanent mana sources: rocks, mana dorks, etc.
                    oracle_text ILIKE '%{T}: Add%'
                    -- Land tutors: "search ... land ... battlefield"
                    OR oracle_text ~* 'search[^\n]*land[^\n]*battlefield'
                    -- Basic land-type tutors (Nature''s Lore, Farseek, Skyshroud Claim, etc.)
                    OR oracle_text ~* 'search[^\n]*(forest|plains|island|swamp|mountain|wastes)[^\n]*battlefield'
                  )
                """
            )
            rows = cur.fetchall()

    ramp_ids: frozenset = frozenset(card_id for card_id, _ in rows)
    guaranteed: dict[str, str] = {}
    for card_id, name in rows:
        if name in ("Sol Ring", "Arcane Signet") and name not in guaranteed:
            guaranteed[name] = card_id

    log.info("Found %d ramp cards (%d guaranteed)", len(ramp_ids), len(guaranteed))
    result = (ramp_ids, guaranteed)
    _ramp_cache[db_url] = result
    return result


_LAND_STAPLE_NAMES = ("Command Tower", "Exotic Orchard")


def get_land_staple_ids(db_url: str) -> dict[str, str]:
    """Return {name: card_id} for auto-include non-basic land staples.

    Command Tower and Exotic Orchard are near-universal inclusions.
    Both have colorless color identity and are legal in every Commander deck.
    """
    if db_url in _land_staple_cache:
        return _land_staple_cache[db_url]

    with _get_conn(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (name) name, id::text
                FROM cards
                WHERE name = ANY(%s)
                ORDER BY name, id
                """,
                (list(_LAND_STAPLE_NAMES),),
            )
            rows = cur.fetchall()

    result: dict[str, str] = {name: card_id for name, card_id in rows}
    log.info("Loaded %d land staple IDs", len(result))
    _land_staple_cache[db_url] = result
    return result


def legal_card_ids(
    commander_id: str,
    all_ids: list[str],
    color_identities: dict[str, frozenset],
    partner_ids: list[str] | None = None,
) -> list[str]:
    """Return card IDs whose color identity is a subset of the commander's.

    Pass partner_ids for partner-commander pairs so the full union color
    identity is used (e.g. Krark+Sakashima → R∪U, not just R).
    """
    cmd_ci = color_identities.get(commander_id, frozenset())
    for pid in (partner_ids or []):
        cmd_ci = cmd_ci | color_identities.get(pid, frozenset())
    return [
        cid for cid in all_ids
        if color_identities.get(cid, frozenset()) <= cmd_ci
    ]


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


# ── Proxy context for unseen commanders ──────────────────────────────────────

def get_proxy_context_from_similar_commanders(
    commander_id: str,
    db_url: str,
    embeddings: dict[str, np.ndarray],
    top_k: int = 3,
    max_cards: int = 20,
    min_freq: float = 0.5,
) -> list[str]:
    """Return common staple cards from the most embedding-similar commanders with training decks.

    Used as a fallback when ``get_common_context`` returns empty (i.e., no decks
    have been imported for this commander yet).  Instead of leaving the decoder
    with only the commander embedding as context — which produces generic-staple
    biased scores — we seed it with cards that real players put alongside the
    *most similar* commanders they **have** trained on.

    Similarity is cosine distance in the Phase-1 embedding space, so an Elf
    planeswalker like Tyvar the Bellicose will naturally cluster near Lathril /
    Ezuri / other Elf commanders whose oracle texts share the same vocabulary.
    Those commanders' shared staples (Llanowar Elves, Elvish Archdruid, etc.)
    then prime the decoder to score Elf-tribal cards appropriately.

    Parameters
    ----------
    commander_id:
        Card UUID (str) of the commander to find proxy context for.
    db_url:
        Synchronous psycopg2-compatible database URL.
    embeddings:
        Full card embedding dict (output of ``get_embeddings``).
    top_k:
        Number of most-similar commanders to aggregate decks from.
    max_cards:
        Maximum number of proxy context cards to return.
    min_freq:
        Minimum fraction of the aggregated decks a card must appear in to be
        included.  Higher values → fewer but more universally-played staples.

    Returns
    -------
    list[str]
        Card IDs sorted by frequency descending, capped at ``max_cards``.
        Empty if no suitable proxy commanders exist.
    """
    if commander_id not in embeddings:
        return []

    # Collect all commanders that have at least one imported deck.
    with _get_conn(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT commander_id::text
                FROM decks
                WHERE commander_id IS NOT NULL
                """
            )
            rows = cur.fetchall()

    deck_commander_ids: list[str] = [r[0] for r in rows if r[0] != commander_id]
    # ^ Skip the target commander itself in case it somehow has a deck (shouldn't happen for
    # unseen commanders, but avoids a trivial self-similarity edge case).
    if not deck_commander_ids:
        return []

    # Rank by cosine similarity in the raw (pre-model) embedding space.
    cmd_vec = embeddings[commander_id].astype(np.float32)
    cmd_norm = np.linalg.norm(cmd_vec)
    if cmd_norm == 0:
        return []
    cmd_unit = cmd_vec / cmd_norm

    sims: list[tuple[str, float]] = []
    for cid in deck_commander_ids:
        if cid not in embeddings:
            continue
        other = embeddings[cid].astype(np.float32)
        other_norm = np.linalg.norm(other)
        if other_norm == 0:
            continue
        sim = float(np.dot(cmd_unit, other / other_norm))
        sims.append((cid, sim))

    sims.sort(key=lambda x: x[1], reverse=True)
    proxy_commander_ids = [cid for cid, _ in sims[:top_k]]

    if not proxy_commander_ids:
        return []

    # Aggregate decks from those proxy commanders and find frequent staples.
    with _get_conn(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ARRAY(SELECT unnest(card_ids)::text) AS card_ids
                FROM decks
                WHERE commander_id::text = ANY(%s)
                """,
                (proxy_commander_ids,),
            )
            deck_rows = cur.fetchall()

    if not deck_rows:
        return []

    n_decks = len(deck_rows)
    freq: dict[str, int] = {}
    for (card_ids,) in deck_rows:
        for cid in (card_ids or []):
            freq[cid] = freq.get(cid, 0) + 1

    threshold = min_freq * n_decks
    common = [
        (cid, count)
        for cid, count in freq.items()
        # Exclude the unseen commander itself from proxy context cards (belt-and-suspenders:
        # commanders are rarely in their own deck lists, but guard against dirty data).
        if count >= threshold and cid != commander_id
    ]
    common.sort(key=lambda x: x[1], reverse=True)
    log.info(
        "Proxy context for %s: using %d similar commanders, found %d candidates (>= %.0f%% freq)",
        commander_id, len(proxy_commander_ids), len(common), min_freq * 100,
    )
    return [cid for cid, _ in common[:max_cards]]


# ── Card scoring ──────────────────────────────────────────────────────────────

def score_cards(
    commander_id: str,
    context_ids: list[str],
    embeddings: dict[str, np.ndarray],
    model: DeckConstructor,
    all_ids: list[str],
    color_identities: dict[str, frozenset] | None = None,
    batch_size: int = 512,
    partner_ids: list[str] | None = None,
) -> list[tuple[str, float]]:
    """Score color-legal cards given commander + context, return sorted (card_id, score).

    Strictly filters to cards whose color identity is a subset of the commander's.
    Excludes the commander itself and any cards already in context_ids.
    If context is empty, uses the commander embedding as the single context token
    (the DeckConstructor requires at least 1 token in the sequence).
    Pass partner_ids for partner-commander pairs to use the union color identity.
    """
    exclude = {commander_id} | set(context_ids)

    if color_identities:
        cmd_ci = color_identities.get(commander_id, frozenset())
        for pid in (partner_ids or []):
            cmd_ci = cmd_ci | color_identities.get(pid, frozenset())
        candidate_ids = [
            cid for cid in all_ids
            if cid not in exclude
            and cid in embeddings
            and color_identities.get(cid, frozenset()) <= cmd_ci
        ]
    else:
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
    embedding_model: str = "sentence-transformers/all-mpnet-base-v2",
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
