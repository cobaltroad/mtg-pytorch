"""
Export the compositional training artifact from the database.

Produces /data/mtg_dataset_compositional.pt — a self-contained artifact for
the compositional training path (issue #71).  The GPU machine (Windows, no
Docker, no DB) downloads this file and trains all four phases from it.

Artifact contents
-----------------
  meta                    – provenance: model, dim, card count, pair count, …
  card_ids                – list[str], N card UUIDs in index order
  embeddings              – Tensor(N, D) float32
  functional_pairs        – dict with a_idx / b_idx int32 tensors (Phase 1)
  synergy                 – a_idx / b_idx / labels (Phase 2, same as co-occ artifact)
  decks                   – list[dict] (Phase 3/4, same schema as co-occ artifact)
  synergy_positions       – list[dict] (Phase 4, same schema as co-occ artifact)
  color_identities        – {card_id: [colors]} for legal-neg reconstruction

Functional equivalence classes (Phase 1)
-----------------------------------------
Two cards are in the same class when they share:
  • The same dominant ability role (ability_name from card_abilities where
    ability_type = 'role') — e.g. 'ramp', 'removal', 'repeatable_draw'
  • The same color identity bucket (sorted color-identity string; 'C' for
    colorless)
  • The same CMC bracket (floor(cmc / 2))

Each class with ≥ 2 embedded members yields up to MAX_PER_CLASS positive
pairs, capped to prevent high-frequency roles (e.g. 'ramp') from dominating.

Usage
-----
    python export_dataset_compositional.py

Environment variables
---------------------
  DATASET_OUTPUT_COMP    Output path  (default /data/mtg_dataset_compositional.pt)
  COMP_MAX_PER_CLASS     Max pairs per equivalence class   (default 50)
  DATASET_SAMPLE         Max ability_trigger positives     (default 500 000)
  DATASET_ROLE_SAMPLE    Max role_demand positives         (default 100 000)
  DATASET_COMBO_SAMPLE   Max combo pair positives          (default 200 000)
  DATASET_CV_SAMPLE      Max commander_value positives     (default 200 000)
  DATASET_NEG_RATIO      Negatives per positive            (default 3)
  DATASET_HARD_NEG_FRAC  Fraction of negs that are hard   (default 0.5)
  DATASET_SYN_LIMIT      Max synergy positions/commander   (default 300)
"""

from __future__ import annotations

import itertools
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

# Re-use the co-occurrence export helpers to avoid duplication.
from export_dataset import (
    _load_embeddings,
    _load_synergy_pairs,
    _load_color_identities,
    _load_decks,
    _build_synergy_positions,
    get_conn,
)

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

OUTPUT_PATH   = Path(os.environ.get("DATASET_OUTPUT_COMP", "/data/mtg_dataset_compositional.pt"))
MAX_PER_CLASS = int(os.environ.get("COMP_MAX_PER_CLASS", "50"))


def _color_identity_bucket(color_identity: list[str] | None) -> str:
    """Sorted color-identity string; 'C' for colorless."""
    if not color_identity:
        return "C"
    return "".join(sorted(c.upper() for c in color_identity))


def _build_functional_pairs(
    card_ids: list[str],
    id_to_idx: dict[str, int],
    max_per_class: int = MAX_PER_CLASS,
) -> tuple[np.ndarray, np.ndarray]:
    """Build Phase 1 functional equivalence pairs from card_abilities.

    Two cards are placed in the same equivalence class when they share a
    dominant ability role, color identity bucket, and CMC bracket.

    Returns (a_idx, b_idx) int32 arrays — indices into card_ids.
    """
    log.info("Building functional equivalence pairs (max_per_class=%d)…", max_per_class)

    embedded = set(id_to_idx.keys())

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("""
                SELECT
                    ca.card_id::text   AS card_id,
                    ca.ability_name    AS role,
                    c.color_identity   AS color_identity,
                    COALESCE(c.cmc, 0) AS cmc
                FROM card_abilities ca
                JOIN cards c ON c.id = ca.card_id
                WHERE ca.ability_type = 'role'
            """)
            rows = cur.fetchall()

    # Group card indices by equivalence class key.
    classes: dict[tuple, list[int]] = defaultdict(list)
    seen_per_class: dict[tuple, set[int]] = defaultdict(set)
    for row in rows:
        card_id = row["card_id"]
        if card_id not in embedded:
            continue
        idx = id_to_idx[card_id]
        bucket = _color_identity_bucket(row["color_identity"])
        cmc_bracket = int(math.floor(float(row["cmc"]))) // 2
        key = (row["role"], bucket, cmc_bracket)
        if idx not in seen_per_class[key]:
            classes[key].append(idx)
            seen_per_class[key].add(idx)

    # Generate pairs, capped per class.
    a_list: list[int] = []
    b_list: list[int] = []
    n_classes_used = 0

    for key, members in classes.items():
        if len(members) < 2:
            continue
        n_classes_used += 1
        all_pairs = list(itertools.combinations(members, 2))
        if len(all_pairs) > max_per_class:
            random.shuffle(all_pairs)
            all_pairs = all_pairs[:max_per_class]
        for a, b in all_pairs:
            a_list.append(a)
            b_list.append(b)

    log.info(
        "Functional pairs: %d pairs from %d / %d classes",
        len(a_list), n_classes_used, len(classes),
    )

    return (
        np.array(a_list, dtype=np.int32),
        np.array(b_list, dtype=np.int32),
    )


def main() -> None:
    # 1. Embeddings
    card_ids, emb_matrix = _load_embeddings()
    n         = len(card_ids)
    id_to_idx = {cid: i for i, cid in enumerate(card_ids)}
    norms     = np.linalg.norm(emb_matrix, axis=1, keepdims=True)
    normed    = (emb_matrix / np.maximum(norms, 1e-8)).astype(np.float32)

    # 2. Functional pairs (Phase 1)
    fp_a, fp_b = _build_functional_pairs(card_ids, id_to_idx)

    # 3. Synergy pairs (Phase 2) — same as co-occurrence artifact
    a_idx, b_idx, labels = _load_synergy_pairs(id_to_idx, normed)

    # 4. Decks + positions (Phases 3/4) — same as co-occurrence artifact
    color_ids  = _load_color_identities(id_to_idx)
    decks      = _load_decks(card_ids, id_to_idx, color_ids)
    positions  = _build_synergy_positions(decks, card_ids, id_to_idx, color_ids)

    # 5. Assemble and save
    commander_count = len({p["commander_idx"] for p in positions})
    meta = {
        "model":                  os.environ.get("EMBEDDING_MODEL", "sentence-transformers/all-mpnet-base-v2"),
        "dim":                    int(emb_matrix.shape[1]),
        "card_count":             n,
        "functional_pair_count":  int(len(fp_a)),
        "synergy_count":          int(len(a_idx)),
        "deck_count":             len(decks),
        "position_count":         len(positions),
        "commander_count":        commander_count,
        "training_path":          "compositional",
        "created_at":             datetime.now(timezone.utc).isoformat(),
    }

    artifact = {
        "meta":      meta,
        "card_ids":  card_ids,
        "embeddings": torch.from_numpy(emb_matrix),
        "functional_pairs": {
            "a_idx": torch.from_numpy(fp_a),
            "b_idx": torch.from_numpy(fp_b),
        },
        "synergy": {
            "a_idx":  torch.from_numpy(a_idx),
            "b_idx":  torch.from_numpy(b_idx),
            "labels": torch.from_numpy(labels),
        },
        "decks":             decks,
        "synergy_positions": positions,
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
        "Done. %.1f MB  |  %d cards  |  %d functional pairs  |  %d synergy pairs  "
        "|  %d decks  |  %d positions",
        size_mb, n, len(fp_a), len(a_idx), len(decks), len(positions),
    )


if __name__ == "__main__":
    main()
