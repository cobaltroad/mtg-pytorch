"""
Shared database helpers for the export_* family of scripts.

Provides connection management, embedding loading, and synergy pair
construction.  Imported by:

    export_dataset.py               – compositional artifact (Phases 1–2)
    export_cooccurrence_dataset.py  – co-occurrence artifact (Phases 1–4)
    export_dataset_commanders.py    – commanders artifact    (Phases 3–4)
"""

from __future__ import annotations

import logging
import math
import os
import random
from collections import defaultdict

import numpy as np
import psycopg2
import psycopg2.extras

log = logging.getLogger(__name__)

DATABASE_URL    = os.environ["DATABASE_URL"]
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "sentence-transformers/all-mpnet-base-v2")

SAMPLE_PER_EVENT  = int(os.environ.get("DATASET_SAMPLE_PER_EVENT",
                         os.environ.get("DATASET_SAMPLE", "100000")))
ROLE_SAMPLE       = int(os.environ.get("DATASET_ROLE_SAMPLE",       "100000"))
COMBO_SAMPLE      = int(os.environ.get("DATASET_COMBO_SAMPLE",      "200000"))
CV_SAMPLE         = int(os.environ.get("DATASET_CV_SAMPLE",         "200000"))
EFFECT_PEER_SAMPLE = int(os.environ.get("DATASET_EFFECT_PEER_SAMPLE", "200000"))
NEG_RATIO         = int(os.environ.get("DATASET_NEG_RATIO",    "3"))
HARD_NEG_FRAC     = float(os.environ.get("DATASET_HARD_NEG_FRAC", "0.5"))
SYN_LIMIT         = int(os.environ.get("DATASET_SYN_LIMIT",    "300"))


# ── Connection ────────────────────────────────────────────────────────────────

def _sync_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


def get_conn():
    return psycopg2.connect(_sync_dsn(DATABASE_URL))


# ── Embeddings ────────────────────────────────────────────────────────────────

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


# ── Card metadata ─────────────────────────────────────────────────────────────

def _load_card_meta(id_to_idx: dict[str, int]) -> dict[str, dict]:
    """Return {card_id: {name, mana_cost, type_line, cmc, color_identity}} for all embedded cards."""
    ids = list(id_to_idx.keys())
    result: dict[str, dict] = {}
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                "SELECT id::text, name, mana_cost, type_line, cmc, color_identity"
                " FROM cards WHERE id::text = ANY(%s)",
                (ids,),
            )
            for row in cur.fetchall():
                result[row["id"]] = {
                    "name":           row["name"],
                    "mana_cost":      row["mana_cost"] or "",
                    "type_line":      row["type_line"] or "",
                    "cmc":            float(row["cmc"]) if row["cmc"] is not None else None,
                    "color_identity": sorted(row["color_identity"] or []),
                }
    return result


# ── Color identities ──────────────────────────────────────────────────────────

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


# ── Synergy pairs (Phase 2) ───────────────────────────────────────────────────

def _load_effect_peer_pairs(
    id_to_idx: dict[str, int],
    sample: int = EFFECT_PEER_SAMPLE,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (a_idx, b_idx) int32 arrays for effect_peer synergy edges.

    Stratified by (trigger_event/effect_class) bucket so large buckets
    (e.g. creature_etb/draw) don't crowd out smaller ones.  No negatives
    are included — callers use these as direct positive pairs.

    Intended for the compositional artifact where effect_peer is stored as a
    separate key so the trainer can use pairs directly rather than routing
    them through the producer-grouping step (which silently discards them).
    """
    log.info("Loading effect_peer pairs (sample=%d)…", sample)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT card_a::text, card_b::text,
                       (metadata->>'trigger_event') || '/' || (metadata->>'effect_class') AS bucket
                FROM synergy_edges TABLESAMPLE SYSTEM(10)
                WHERE score_type = 'effect_peer'
            """)
            ep_by_bucket: dict[str, list[tuple[str, str]]] = defaultdict(list)
            for card_a, card_b, bucket in cur.fetchall():
                ep_by_bucket[bucket].append((card_a, card_b))

    per_bucket = max(1, sample // max(len(ep_by_bucket), 1))
    a_list: list[int] = []
    b_list: list[int] = []
    for bucket, bucket_rows in sorted(ep_by_bucket.items()):
        random.shuffle(bucket_rows)
        n_added = 0
        for card_a, card_b in bucket_rows[:per_bucket]:
            if card_a in id_to_idx and card_b in id_to_idx:
                a_list.append(id_to_idx[card_a])
                b_list.append(id_to_idx[card_b])
                n_added += 1
        log.info("    %-55s  %6d pairs  (pool %d)", bucket, n_added, len(bucket_rows))

    log.info("  %d effect_peer pairs across %d buckets", len(a_list), len(ep_by_bucket))
    return (
        np.array(a_list, dtype=np.int32),
        np.array(b_list, dtype=np.int32),
    )


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
    ability_score_type: str = "ability_trigger",
    include_commander_value: bool = False,
    include_effect_peer: bool = False,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (a_idx, b_idx, labels) int32/float32 arrays.

    Covers ability edges (score_type controlled by *ability_score_type*),
    role_demand, combo, and optionally commander_value or effect_peer, plus
    pre-mined hard negatives and random negatives.

    Args:
        ability_score_type: ``'ability_trigger'`` for the co-occurrence path
            (oracle-text pattern edges); ``'xmage_ability_trigger'`` for the
            compositional path (raw XMage class-name edges, no translation).
        include_commander_value: Whether to include ``commander_value`` synergy
            edges.  Defaults to ``False`` — those edges are covered by
            ``export_dataset_commanders.py``.  Pass ``True`` for the
            co-occurrence path which does not use that artifact.
        include_effect_peer: Whether to include ``effect_peer`` synergy edges
            (cards sharing the same (trigger_event, effect_class) bucket).
            Defaults to ``False``.  Pass ``True`` for the compositional path
            to separate Beast Whisperer from Impact Tremors in Phase 2.
    """
    log.info(
        "Loading synergy pairs (ability_score_type=%s, per_event=%d, role=%d, combo=%d%s%s)…",
        ability_score_type, SAMPLE_PER_EVENT, ROLE_SAMPLE, COMBO_SAMPLE,
        f", cv={CV_SAMPLE}" if include_commander_value else "",
        f", ep={EFFECT_PEER_SAMPLE}" if include_effect_peer else "",
    )
    positives: list[tuple[int, int, float]] = []

    with get_conn() as conn:
        with conn.cursor() as cur:
            # ability_trigger — stratified per trigger_event.
            #
            # A flat TABLESAMPLE SYSTEM(10) LIMIT N starves rare events (e.g.
            # adapt_evolve, sac_outlet) when high-volume events like creature_etb
            # dominate the table.  Instead we:
            #   1. Fetch a 10 % random sample of all ability_trigger rows once.
            #   2. Group by trigger_event in Python, shuffle each group, cap at
            #      SAMPLE_PER_EVENT.
            # This gives every event proportional representation without N
            # round-trips to the database.
            event_key_expr = (
                "COALESCE(metadata->>'ability_class', 'unknown')"
                if ability_score_type == "xmage_ability_trigger"
                else "COALESCE(metadata->>'trigger_event', 'unknown')"
            )
            log.info("  Fetching ~10%% sample of %s edges…", ability_score_type)
            cur.execute(f"""
                SELECT card_a::text, card_b::text,
                       {event_key_expr} AS te
                FROM synergy_edges TABLESAMPLE SYSTEM(10)
                WHERE score_type = %s
            """, (ability_score_type,))
            rows_by_event: dict[str, list[tuple[str, str]]] = defaultdict(list)
            for card_a, card_b, te in cur.fetchall():
                rows_by_event[te].append((card_a, card_b))

            ability_start = len(positives)
            for te, event_rows in sorted(rows_by_event.items()):
                random.shuffle(event_rows)
                n_added = 0
                for card_a, card_b in event_rows[:SAMPLE_PER_EVENT]:
                    if card_a in id_to_idx and card_b in id_to_idx:
                        positives.append((id_to_idx[card_a], id_to_idx[card_b], 1.0))
                        n_added += 1
                log.info("    %-45s  %6d pairs  (pool %d)", te, n_added, len(event_rows))

            total_ability = len(positives) - ability_start
            log.info("  %d ability_trigger pairs across %d events",
                     total_ability, len(rows_by_event))

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

            # commander_value (sampled) — skipped by default; the commanders
            # artifact (export_dataset_commanders.py) already covers these edges.
            if include_commander_value and CV_SAMPLE > 0:
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

            # effect_peer — cards sharing (trigger_event, effect_class).
            # Stratified per bucket so large buckets (creature_cast/draw) don't
            # crowd out small ones.  Only meaningful for the compositional path.
            # NOTE: prefer _load_effect_peer_pairs() for the compositional artifact
            # so effect_peer is stored as a separate key and used directly by the
            # trainer (bypassing the producer-grouping step that wastes peer edges).
            if include_effect_peer and EFFECT_PEER_SAMPLE > 0:
                ep_a, ep_b = _load_effect_peer_pairs(id_to_idx)
                ep_pairs = list(zip(ep_a.tolist(), ep_b.tolist()))
                for a_i, b_i in ep_pairs:
                    positives.append((a_i, b_i, 1.0))
                log.info("  + %d effect_peer pairs", len(ep_pairs))

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

    log.info("  %d negative pairs (%d hard, %d random)",
             len(hard_negs) + len(rand_negs), len(hard_negs), len(rand_negs))

    all_pairs = positives + hard_negs + rand_negs
    random.shuffle(all_pairs)

    return (
        np.array([p[0] for p in all_pairs], dtype=np.int32),
        np.array([p[1] for p in all_pairs], dtype=np.int32),
        np.array([p[2] for p in all_pairs], dtype=np.float32),
    )
