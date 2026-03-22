"""
Co-occurrence training path — data loading.

Supervision signal: human deck membership (Phase 3) and ability-trigger
synergy edges derived from oracle-text pattern matching (Phase 2).

This is the existing training path.  Checkpoints are prefixed ``phase``.
"""

from __future__ import annotations

import logging
import os
import random
from collections import Counter, defaultdict

import numpy as np
import psycopg2
import psycopg2.extras

log = logging.getLogger(__name__)

DATABASE_URL   = os.environ.get("DATABASE_URL", "")
EMBEDDING_MODEL = os.environ.get(
    "EMBEDDING_MODEL", "sentence-transformers/all-mpnet-base-v2"
)

CHECKPOINT_PREFIX = "phase"


def warm_start_name(phase: int) -> str:
    """Return the checkpoint name to warm-start from for the given phase."""
    return {2: "phase1_best", 3: "phase2_best", 4: "phase3_best"}.get(phase, "")


def _get_conn():
    return psycopg2.connect(DATABASE_URL)


# ── Phase 2 data ───────────────────────────────────────────────────────────────

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
    role_demand_sample: int = 100_000,
    combo_sample: int = 200_000,
    commander_value_sample: int = 200_000,
) -> list[tuple[str, str, float]]:
    """Return [(card_a_id, card_b_id, label)] with balanced pos/neg pairs.

    Positives: ability_trigger, role_demand, commander_value, and combo_package
    edges from synergy_edges.  Negatives: hard (nearest-neighbour) + random.
    """
    log.info(
        "Loading synergy pairs (ability_trigger=%d, role_demand=%d, combo=%d, "
        "commander_value=%d)…",
        sample, role_demand_sample, combo_sample, commander_value_sample,
    )
    with _get_conn() as conn:
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

            if role_demand_sample > 0:
                cur.execute("""
                    SELECT card_a::text, card_b::text, score
                    FROM synergy_edges
                    WHERE score_type = 'role_demand'
                    LIMIT %s
                """, (role_demand_sample,))
                role_pairs = [
                    (r[0], r[1], float(r[2])) for r in cur.fetchall()
                    if r[0] in embeddings and r[1] in embeddings
                ]
                log.info("  %d ability_trigger + %d role_demand pairs",
                         len(positives), len(role_pairs))
                positives = positives + role_pairs
            else:
                log.info("  %d ability_trigger pairs (role_demand disabled)", len(positives))

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


# ── Phase 3 data ───────────────────────────────────────────────────────────────

def load_color_identities(embeddings: dict[str, np.ndarray]) -> dict[str, frozenset]:
    """Return {card_id: frozenset of color letters} for every embedded card."""
    ids = list(embeddings.keys())
    result: dict[str, frozenset] = {}
    with _get_conn() as conn:
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
    import json

    with _get_conn() as conn:
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
            metadata = json.loads(metadata)
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


# ── Phase 4 data ───────────────────────────────────────────────────────────────

def load_synergy_positions(
    decks: list[dict],
    embeddings: dict[str, np.ndarray],
    combo_weight: float = 3.0,
    ability_weight: float = 2.0,
    tribal_weight: float = 1.5,
    synergy_limit_per_commander: int = 300,
) -> list[dict]:
    """Build synthetic training positions from combo packages and synergy edges."""
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

    with _get_conn() as conn:
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

    with _get_conn() as conn:
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
