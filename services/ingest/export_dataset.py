"""
Export a self-contained training artifact from the database.

Produces /data/mtg_dataset.pt containing everything needed to run all four
training phases on a GPU machine with no live database connection.

Artifact contents
-----------------
  meta               – provenance: model name, dim, counts, created_at
  card_ids           – list[str], N card UUIDs in index order
  embeddings         – Tensor(N, 768) float32
  synergy.a_idx      – Tensor int32, phase 2 pair indices
  synergy.b_idx      – Tensor int32
  synergy.labels     – Tensor float32 (soft labels; hard negs = 0.0)
  decks              – list[dict] for phase 3/4 (indices + legal pool)
  synergy_positions  – list[dict] for phase 4 (pre-computed positions)

A companion JSON sidecar (mtg_dataset.json) is written alongside the .pt
file so the API can serve metadata without loading the full artifact.

Usage
-----
    python export_dataset.py

Environment variables
---------------------
  DATASET_SAMPLE         Max ability_trigger positives (default 500 000)
  DATASET_ROLE_SAMPLE    Max role_demand positives     (default 100 000)
  DATASET_COMBO_SAMPLE   Max combo pair positives      (default 200 000)
  DATASET_CV_SAMPLE      Max commander_value positives (default 200 000)
  DATASET_NEG_RATIO      Negatives per positive        (default 3)
  DATASET_HARD_NEG_FRAC  Fraction of negs that are hard (default 0.5)
  DATASET_SYN_LIMIT      Max synergy positions/cmd     (default 300)
  DATASET_OUTPUT         Output path  (default /data/mtg_dataset.pt)
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import psycopg2
import psycopg2.extras
import torch

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DATABASE_URL    = os.environ["DATABASE_URL"]
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "sentence-transformers/all-mpnet-base-v2")
OUTPUT_PATH     = Path(os.environ.get("DATASET_OUTPUT", "/data/mtg_dataset.pt"))
SAMPLE          = int(os.environ.get("DATASET_SAMPLE",       "500000"))
ROLE_SAMPLE     = int(os.environ.get("DATASET_ROLE_SAMPLE",  "100000"))
COMBO_SAMPLE    = int(os.environ.get("DATASET_COMBO_SAMPLE", "200000"))
CV_SAMPLE       = int(os.environ.get("DATASET_CV_SAMPLE",    "200000"))
NEG_RATIO       = int(os.environ.get("DATASET_NEG_RATIO",    "3"))
HARD_NEG_FRAC   = float(os.environ.get("DATASET_HARD_NEG_FRAC", "0.5"))
SYN_LIMIT       = int(os.environ.get("DATASET_SYN_LIMIT",    "300"))


def _sync_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


def get_conn():
    return psycopg2.connect(_sync_dsn(DATABASE_URL))


# ── Step 1: Embeddings ────────────────────────────────────────────────────────

def _load_embeddings() -> tuple[list[str], np.ndarray]:
    """Return (card_ids, float32 matrix shaped (N, D))."""
    log.info("Loading embeddings (model=%s)…", EMBEDDING_MODEL)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT card_id::text, embedding FROM card_embeddings WHERE model = %s",
                (EMBEDDING_MODEL,),
            )
            rows = cur.fetchall()

    card_ids: list[str] = []
    vecs: list[np.ndarray] = []
    for card_id, vec in rows:
        if isinstance(vec, str):
            vec = np.fromstring(vec.strip("[]"), sep=",", dtype=np.float32)
        else:
            vec = np.array(vec, dtype=np.float32)
        card_ids.append(card_id)
        vecs.append(vec)

    log.info("Loaded %d embeddings (dim=%d)", len(card_ids), vecs[0].shape[0] if vecs else 0)
    return card_ids, np.stack(vecs).astype(np.float32)


# ── Step 2: Synergy pairs ─────────────────────────────────────────────────────

def _mine_hard_negatives(
    positives: list[tuple[int, int, float]],
    normed: np.ndarray,
    pos_set: set[tuple[int, int]],
    n_hard: int,
    top_k: int = 200,
) -> list[tuple[int, int, float]]:
    log.info("Mining %d hard negatives (top_k=%d)…", n_hard, top_k)
    unique_a = list({a for a, _, _ in positives})
    random.shuffle(unique_a)
    per_anchor = max(1, math.ceil(n_hard / max(len(unique_a), 1)))

    hard_negs: list[tuple[int, int, float]] = []
    for a_idx in unique_a:
        if len(hard_negs) >= n_hard:
            break
        a_vec = normed[a_idx]
        sims = normed @ a_vec
        ranked = np.argsort(sims)[::-1]
        collected = 0
        for raw_idx in ranked[1: top_k + 1]:
            b_idx = int(raw_idx)
            if (a_idx, b_idx) not in pos_set and (b_idx, a_idx) not in pos_set:
                hard_negs.append((a_idx, b_idx, 0.0))
                collected += 1
                if collected >= per_anchor or len(hard_negs) >= n_hard:
                    break

    log.info("  %d hard negatives mined", len(hard_negs))
    return hard_negs


def _load_synergy_pairs(
    id_to_idx: dict[str, int],
    normed: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (a_idx, b_idx, labels) int32/float32 arrays.

    Covers all score types (ability_trigger, role_demand, combo, commander_value)
    plus pre-mined hard negatives and random negatives.  The sampling parameters
    mirror those in train.py so the artifact reflects what a DB-connected run
    would produce.
    """
    log.info(
        "Loading synergy pairs (ability=%d, role=%d, combo=%d, cv=%d)…",
        SAMPLE, ROLE_SAMPLE, COMBO_SAMPLE, CV_SAMPLE,
    )
    positives: list[tuple[int, int, float]] = []

    with get_conn() as conn:
        with conn.cursor() as cur:
            # ability_trigger (sampled — table can be very large)
            cur.execute("""
                SELECT card_a::text, card_b::text
                FROM synergy_edges TABLESAMPLE SYSTEM(10)
                WHERE score_type = 'ability_trigger'
                LIMIT %s
            """, (SAMPLE,))
            positives += [
                (id_to_idx[r[0]], id_to_idx[r[1]], 1.0)
                for r in cur.fetchall()
                if r[0] in id_to_idx and r[1] in id_to_idx
            ]
            log.info("  %d ability_trigger pairs", len(positives))

            # role_demand (stored score as soft label)
            if ROLE_SAMPLE > 0:
                cur.execute("""
                    SELECT card_a::text, card_b::text, score
                    FROM synergy_edges
                    WHERE score_type = 'role_demand'
                    LIMIT %s
                """, (ROLE_SAMPLE,))
                role_pairs = [
                    (id_to_idx[r[0]], id_to_idx[r[1]], float(r[2]))
                    for r in cur.fetchall()
                    if r[0] in id_to_idx and r[1] in id_to_idx
                ]
                positives += role_pairs
                log.info("  + %d role_demand pairs", len(role_pairs))

            # combo_package pairs — all cards sharing a package are strong positives
            if COMBO_SAMPLE > 0:
                cur.execute("""
                    SELECT a.card_id::text, b.card_id::text
                    FROM combo_package_cards a
                    JOIN combo_package_cards b
                      ON a.combo_package_id = b.combo_package_id
                     AND a.card_id < b.card_id
                    WHERE a.card_id IS NOT NULL AND b.card_id IS NOT NULL
                      AND a.is_template = FALSE AND b.is_template = FALSE
                    LIMIT %s
                """, (COMBO_SAMPLE,))
                combo_pairs = [
                    (id_to_idx[r[0]], id_to_idx[r[1]], 1.0)
                    for r in cur.fetchall()
                    if r[0] in id_to_idx and r[1] in id_to_idx
                ]
                positives += combo_pairs
                log.info("  + %d combo pairs", len(combo_pairs))

            # commander_value (sampled)
            if CV_SAMPLE > 0:
                cur.execute("""
                    SELECT card_a::text, card_b::text, score
                    FROM synergy_edges TABLESAMPLE SYSTEM(10)
                    WHERE score_type = 'commander_value'
                    LIMIT %s
                """, (CV_SAMPLE,))
                cv_pairs = [
                    (id_to_idx[r[0]], id_to_idx[r[1]], float(r[2]))
                    for r in cur.fetchall()
                    if r[0] in id_to_idx and r[1] in id_to_idx
                ]
                positives += cv_pairs
                log.info("  + %d commander_value pairs", len(cv_pairs))

    log.info("  %d total positive pairs", len(positives))

    pos_set = {(a, b) for a, b, _ in positives}
    n_neg   = len(positives) * NEG_RATIO
    n_hard  = int(n_neg * HARD_NEG_FRAC)
    n_rand  = n_neg - n_hard

    hard_negs = _mine_hard_negatives(positives, normed, pos_set, n_hard)

    all_indices = list(range(len(normed)))
    rand_negs: list[tuple[int, int, float]] = []
    attempts = 0
    while len(rand_negs) < n_rand and attempts < n_rand * 10:
        a, b = random.sample(all_indices, 2)
        if (a, b) not in pos_set and (b, a) not in pos_set:
            rand_negs.append((a, b, 0.0))
        attempts += 1

    log.info("  %d negative pairs (%d hard, %d random)", len(hard_negs) + len(rand_negs), len(hard_negs), len(rand_negs))

    all_pairs = positives + hard_negs + rand_negs
    random.shuffle(all_pairs)

    return (
        np.array([p[0] for p in all_pairs], dtype=np.int32),
        np.array([p[1] for p in all_pairs], dtype=np.int32),
        np.array([p[2] for p in all_pairs], dtype=np.float32),
    )


# ── Step 3: Decks ─────────────────────────────────────────────────────────────

def _load_color_identities(id_to_idx: dict[str, int]) -> dict[str, frozenset]:
    ids = list(id_to_idx.keys())
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


def _load_decks(
    card_ids: list[str],
    id_to_idx: dict[str, int],
    color_ids: dict[str, frozenset],
) -> list[dict]:
    log.info("Loading decks…")
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

    n = len(card_ids)
    _legal_cache: dict[frozenset, np.ndarray] = {}

    def _legal_indices(cmd_ci: frozenset) -> np.ndarray:
        if cmd_ci not in _legal_cache:
            idx = np.array(
                [i for i, cid in enumerate(card_ids)
                 if color_ids.get(cid, frozenset()) <= cmd_ci],
                dtype=np.int64,
            )
            _legal_cache[cmd_ci] = idx if len(idx) > 0 else np.arange(n, dtype=np.int64)
        return _legal_cache[cmd_ci]

    decks = []
    for row in rows:
        cmd_id = row["commander_id"]
        if cmd_id not in id_to_idx:
            continue
        card_id_strs = [str(c) for c in (row["card_ids"] or []) if str(c) in id_to_idx]
        if len(card_id_strs) < 10:
            continue
        cmd_ci = color_ids.get(cmd_id, frozenset())
        metadata = row["metadata"] or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        for pid in metadata.get("partner_commander_ids", []):
            cmd_ci = cmd_ci | color_ids.get(pid, frozenset())
        legal = _legal_indices(cmd_ci)
        decks.append({
            "commander_idx":     id_to_idx[cmd_id],
            "card_idxs":         [id_to_idx[c] for c in card_id_strs],
            "color_identity":    sorted(cmd_ci),
            "legal_neg_indices": torch.from_numpy(legal),
            "archetype":         metadata.get("archetype", "unknown"),
        })

    log.info("Loaded %d decks (%d skipped — commander or cards not embedded)",
             len(decks), len(rows) - len(decks))
    return decks


# ── Step 4: Synergy positions (Phase 4) ───────────────────────────────────────

def _build_synergy_positions(
    decks: list[dict],
    card_ids: list[str],
    id_to_idx: dict[str, int],
    color_ids: dict[str, frozenset],
    combo_weight: float = 3.0,
    ability_weight: float = 2.0,
    tribal_weight: float = 1.5,
) -> list[dict]:
    """Pre-compute Phase 4 synergy positions for ALL legal commanders.

    Covers three position types:

    Combo completions (combo_weight):
        Require deck overlap (≥2 package cards in a human deck) to form
        meaningful context.  Only deck commanders produce these.

    Ability-trigger / tribal (ability_weight / tribal_weight):
        Loaded for every embedded card that is a legal commander appearing in
        synergy_edges — not just those with human decklists.  legal_neg_indices
        are derived from color identity so no deck data is required.

    Storing positions for all commanders (not just ~180 deck commanders)
    exposes the trainer to a much larger commander distribution, which is
    essential for Option A (synergy-only) training.
    """
    from collections import Counter

    log.info(
        "Building Phase 4 synergy positions "
        "(combo=%.1f×, ability=%.1f×, tribal=%.1f×, limit=%d/cmd)…",
        combo_weight, ability_weight, tribal_weight, SYN_LIMIT,
    )

    n = len(card_ids)
    _legal_cache: dict[frozenset, np.ndarray] = {}

    def _legal_indices(cmd_ci: frozenset) -> np.ndarray:
        if cmd_ci not in _legal_cache:
            idx = np.array(
                [i for i, cid in enumerate(card_ids)
                 if color_ids.get(cid, frozenset()) <= cmd_ci],
                dtype=np.int64,
            )
            _legal_cache[cmd_ci] = idx if len(idx) > 0 else np.arange(n, dtype=np.int64)
        return _legal_cache[cmd_ci]

    cmd_legal    = {d["commander_idx"]: d["legal_neg_indices"] for d in decks}
    deck_card_sets = {d["commander_idx"]: set(d["card_idxs"]) for d in decks}

    positions: list[dict] = []

    with get_conn() as conn:
        with conn.cursor() as cur:
            # ── Combo package memberships ─────────────────────────────────────
            cur.execute("""
                SELECT cpc.combo_package_id, cpc.card_id::text
                FROM combo_package_cards cpc
                JOIN combo_packages cp ON cp.id = cpc.combo_package_id
                WHERE cpc.card_id IS NOT NULL
                  AND cpc.is_template = FALSE
                  AND cp.legal_commander = TRUE
            """)
            pkg_map: dict[str, list[int]] = defaultdict(list)
            for pkg_id, card_id in cur.fetchall():
                if card_id in id_to_idx:
                    pkg_map[pkg_id].append(id_to_idx[card_id])

            # ── Ability-trigger / tribal edges for ALL legal commanders ───────
            cur.execute("""
                SELECT se.card_a::text, se.card_b::text, se.score_type,
                       COALESCE(se.metadata->>'trigger_event', '') AS trigger_event
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
            ability_edges: dict[int, list[int]] = defaultdict(list)
            tribal_edges:  dict[int, list[int]] = defaultdict(list)
            for card_a, card_b, score_type, trigger_event in cur.fetchall():
                if card_a not in id_to_idx or card_b not in id_to_idx:
                    continue
                a_idx_v = id_to_idx[card_a]
                b_idx_v = id_to_idx[card_b]
                if score_type == 'tribal_typeline' or trigger_event.startswith("tribal_"):
                    tribal_edges[a_idx_v].append(b_idx_v)
                else:
                    ability_edges[a_idx_v].append(b_idx_v)

    # ── Combo completions (deck-dependent) ────────────────────────────────────
    combo_count = 0
    for deck in decks:
        cmd_idx_v  = deck["commander_idx"]
        in_deck    = deck_card_sets[cmd_idx_v]
        legal      = cmd_legal[cmd_idx_v]
        for pkg_card_list in pkg_map.values():
            pkg_in_deck = [c for c in pkg_card_list if c in in_deck]
            if len(pkg_in_deck) < 2:
                continue
            for target_idx in pkg_card_list:
                context_idxs = [c for c in pkg_in_deck if c != target_idx]
                positions.append({
                    "commander_idx":     cmd_idx_v,
                    "context_card_idxs": context_idxs,
                    "target_card_idx":   target_idx,
                    "weight":            combo_weight,
                    "legal_neg_indices": legal,
                })
                combo_count += 1

    log.info("  %d combo positions (deck commanders only)", combo_count)

    # ── Ability-trigger positions (all commanders) ────────────────────────────
    ability_count = 0
    cmd_ability_count: Counter = Counter()
    for cmd_idx_v, targets in ability_edges.items():
        cmd_ci = color_ids.get(card_ids[cmd_idx_v], frozenset())
        legal  = cmd_legal.get(cmd_idx_v) or torch.from_numpy(_legal_indices(cmd_ci))
        for target_idx in targets[:SYN_LIMIT]:
            positions.append({
                "commander_idx":     cmd_idx_v,
                "context_card_idxs": [],
                "target_card_idx":   target_idx,
                "weight":            ability_weight,
                "legal_neg_indices": legal,
            })
            ability_count += 1
            cmd_ability_count[cmd_idx_v] += 1

    log.info("  %d ability-trigger positions across %d commanders",
             ability_count, len(cmd_ability_count))

    # ── Tribal positions (all commanders) ─────────────────────────────────────
    tribal_count = 0
    cmd_tribal_count: Counter = Counter()
    for cmd_idx_v, targets in tribal_edges.items():
        cmd_ci = color_ids.get(card_ids[cmd_idx_v], frozenset())
        legal  = cmd_legal.get(cmd_idx_v) or torch.from_numpy(_legal_indices(cmd_ci))
        for target_idx in targets[:SYN_LIMIT]:
            positions.append({
                "commander_idx":     cmd_idx_v,
                "context_card_idxs": [],
                "target_card_idx":   target_idx,
                "weight":            tribal_weight,
                "legal_neg_indices": legal,
            })
            tribal_count += 1
            cmd_tribal_count[cmd_idx_v] += 1

    log.info("  %d tribal positions across %d commanders",
             tribal_count, len(cmd_tribal_count))

    all_commanders = len({p["commander_idx"] for p in positions})
    log.info("  %d total positions across %d commanders", len(positions), all_commanders)
    return positions


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    # 1. Embeddings
    card_ids, emb_matrix = _load_embeddings()
    n        = len(card_ids)
    id_to_idx = {cid: i for i, cid in enumerate(card_ids)}
    norms    = np.linalg.norm(emb_matrix, axis=1, keepdims=True)
    normed   = (emb_matrix / np.maximum(norms, 1e-8)).astype(np.float32)

    # 2. Synergy pairs (phase 2)
    a_idx, b_idx, labels = _load_synergy_pairs(id_to_idx, normed)

    # 3. Decks (phase 3/4)
    color_ids = _load_color_identities(id_to_idx)
    decks     = _load_decks(card_ids, id_to_idx, color_ids)

    # 4. Synergy positions (phase 4) — covers all legal commanders, not just deck commanders
    syn_positions = _build_synergy_positions(decks, card_ids, id_to_idx, color_ids)

    # 5. Assemble and save
    commander_count = len({p["commander_idx"] for p in syn_positions})
    meta = {
        "model":            EMBEDDING_MODEL,
        "dim":              int(emb_matrix.shape[1]),
        "card_count":       n,
        "synergy_count":    int(len(a_idx)),
        "deck_count":       len(decks),
        "position_count":   len(syn_positions),
        "commander_count":  commander_count,
        "created_at":       datetime.now(timezone.utc).isoformat(),
    }

    artifact = {
        "meta":      meta,
        "card_ids":  card_ids,
        "embeddings": torch.from_numpy(emb_matrix),
        "synergy": {
            "a_idx":  torch.from_numpy(a_idx),
            "b_idx":  torch.from_numpy(b_idx),
            "labels": torch.from_numpy(labels),
        },
        "decks":             decks,
        "synergy_positions": syn_positions,
        # color_identities enables the trainer to reconstruct legal_neg_indices
        # for any commander without a DB connection — needed for synergy-only Phase 4.
        "color_identities":  {cid: sorted(ci) for cid, ci in color_ids.items()},
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    log.info("Saving artifact → %s", OUTPUT_PATH)
    torch.save(artifact, OUTPUT_PATH)

    meta_path = OUTPUT_PATH.with_suffix(".json")
    meta_path.write_text(json.dumps(meta, indent=2))
    log.info("Metadata sidecar → %s", meta_path)

    size_mb = OUTPUT_PATH.stat().st_size / 1e6
    log.info(
        "Done. %.1f MB  |  %d cards  |  %d pairs  |  %d decks  |  %d positions  |  %d commanders",
        size_mb, n, len(a_idx), len(decks), len(syn_positions), commander_count,
    )


if __name__ == "__main__":
    main()
