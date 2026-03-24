"""
Export a self-contained co-occurrence training artifact from the database.

Produces /data/mtg_cooccurrence_dataset.pt containing everything needed to
run all four training phases on a GPU machine with no live database connection.

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

A companion JSON sidecar (mtg_cooccurrence_dataset.json) is written alongside
the .pt file so the API can serve metadata without loading the full artifact.

Usage
-----
    python export_cooccurrence_dataset.py

Environment variables
---------------------
  DATASET_SAMPLE_PER_EVENT   Max ability_trigger positives per trigger_event
                             (default 100 000).  A 10 % table sample is fetched
                             once, grouped by trigger_event in Python, then each
                             event is capped independently — so rare events like
                             adapt_evolve or sac_outlet receive proportional
                             representation alongside high-volume events like
                             creature_etb.  Total pairs ≈ n_events × cap.
                             Alias: DATASET_SAMPLE (legacy, same semantics).
  DATASET_ROLE_SAMPLE        Max role_demand positives     (default 100 000)
  DATASET_COMBO_SAMPLE       Max combo pair positives      (default 200 000)
  DATASET_CV_SAMPLE          Max commander_value positives (default 200 000)
  DATASET_NEG_RATIO          Negatives per positive        (default 3)
  DATASET_HARD_NEG_FRAC      Fraction of negs that are hard (default 0.5)
  DATASET_SYN_LIMIT          Max synergy positions/cmd     (default 300)
  DATASET_OUTPUT             Output path  (default /data/mtg_cooccurrence_dataset.pt)
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import psycopg2.extras
import torch

from export_db_helpers import (
    EMBEDDING_MODEL,
    SYN_LIMIT,
    _load_card_meta,
    _load_synergy_pairs,
    _load_color_identities,
    _load_embeddings,
    get_conn,
)

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

OUTPUT_PATH = Path(os.environ.get("DATASET_OUTPUT", "/data/mtg_cooccurrence_dataset.pt"))



# ── Step 3: Decks ─────────────────────────────────────────────────────────────

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
        _cached = cmd_legal.get(cmd_idx_v)
        legal  = _cached if _cached is not None else torch.from_numpy(_legal_indices(cmd_ci))
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
        _cached = cmd_legal.get(cmd_idx_v)
        legal  = _cached if _cached is not None else torch.from_numpy(_legal_indices(cmd_ci))
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
    a_idx, b_idx, labels = _load_synergy_pairs(id_to_idx, normed, include_commander_value=True)

    # 2b. Card metadata (name/type for offline eval on GPU machine)
    card_meta = _load_card_meta(id_to_idx)

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
        "training_path":    "cooccurrence",
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
        # card_meta enables offline nearest-neighbour eval on the GPU machine.
        "card_meta":         card_meta,
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
