"""
Export the compositional training artifact from the database.

Produces /data/mtg_dataset.pt — a self-contained artifact for
the compositional training path (issue #71).  The GPU machine (Windows, no
Docker, no DB) downloads this file and trains all four phases from it.

Artifact contents
-----------------
  meta                    – provenance: model, dim, card count, pair count, …
  card_ids                – list[str], N card UUIDs in index order
  embeddings              – Tensor(N, D) float32
  functional_pairs        – dict with a_idx / b_idx int32 tensors (Phase 1)
  synergy                 – a_idx / b_idx / labels (Phase 2)
  card_meta               – {card_id: {name, mana_cost, type_line}} for offline eval

  Phases 3/4 (deck co-occurrence + generative) use the commanders artifact
  produced by export_dataset_commanders.py (mtg_commanders.pt), not this one.

Functional equivalence classes (Phase 1)
-----------------------------------------
Two cards are in the same class when they share the same ability role
(ability_name from card_abilities where ability_type = 'role') — e.g.
'ramp', 'removal', 'repeatable_draw'.  Color identity and CMC are
intentionally excluded: functional equivalence is about what a card does
for the caster, not what it costs or what color it is.  Deckbuilding
constraints are applied downstream.

Each class with ≥ 2 embedded members yields up to MAX_PER_CLASS positive
pairs, capped to prevent high-frequency roles (e.g. 'ramp') from dominating.

Usage
-----
    python export_dataset.py

Environment variables
---------------------
  DATASET_OUTPUT         Output path  (default /data/mtg_dataset.pt)
  COMP_MAX_PER_CLASS     Hard cap on pairs per equivalence class (default 500).
                         Actual pairs per class = min(cap, n*log2(n)) where n
                         is the number of class members.
  DATASET_SAMPLE         Max ability_trigger positives     (default 500 000)
  DATASET_ROLE_SAMPLE    Max role_demand positives         (default 100 000)
  DATASET_COMBO_SAMPLE   Max combo pair positives          (default 200 000)
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

# Re-use the shared DB helpers to avoid duplication.
from export_db_helpers import (
    _load_embeddings,
    _load_card_meta,
    _load_synergy_pairs,
    get_conn,
)

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

OUTPUT_PATH   = Path(os.environ.get("DATASET_OUTPUT", "/data/mtg_dataset.pt"))
MAX_PER_CLASS = int(os.environ.get("COMP_MAX_PER_CLASS", "500"))


def _build_functional_pairs(
    card_ids: list[str],
    id_to_idx: dict[str, int],
    max_per_class: int = MAX_PER_CLASS,
) -> tuple[np.ndarray, np.ndarray]:
    """Build Phase 1 functional equivalence pairs from card_abilities.

    Two cards are placed in the same equivalence class when they share the
    same ability role (e.g. 'removal', 'ramp', 'repeatable_draw').  Color
    identity and CMC are intentionally excluded — functional equivalence is
    about what a card does for the caster, not what it costs or what color it
    is.  Deckbuilding constraints (color, curve) are applied downstream.

    Returns (a_idx, b_idx) int32 arrays — indices into card_ids.
    """
    log.info("Building functional equivalence pairs (max_per_class=%d)…", max_per_class)

    embedded = set(id_to_idx.keys())

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("""
                SELECT
                    ca.card_id::text              AS card_id,
                    ca.ability_name               AS role,
                    COALESCE(ca.effect_class, '') AS effect_class
                FROM card_abilities ca
                WHERE ca.ability_type = 'role'
            """)
            rows = cur.fetchall()

    # Group card indices by (role, effect_class).
    # Cards with a non-null effect_class land in the precise subtype class.
    # Legacy rows with effect_class='' land in a coarse catch-all per role,
    # which will shrink to zero once tag_abilities --rescan has been run.
    classes: dict[tuple[str, str], list[int]] = defaultdict(list)
    seen_per_class: dict[tuple[str, str], set[int]] = defaultdict(set)
    for row in rows:
        card_id = row["card_id"]
        if card_id not in embedded:
            continue
        idx = id_to_idx[card_id]
        key = (row["role"], row["effect_class"])
        if idx not in seen_per_class[key]:
            classes[key].append(idx)
            seen_per_class[key].add(idx)

    # Generate pairs with a proportional cap.
    #
    # Cap per class = min(max_per_class, n * log2(n)) where n is the number of
    # class members.  This gives each member ~log2(n) positive-pair appearances
    # on average, growing naturally with class size.  The hard cap (max_per_class)
    # prevents large classes (draw, combat_trick, ramp) from dominating training.
    #
    # Small classes (n < ~55 at default cap=500) are taken in full because
    # n*log2(n) < all possible pairs before the hard cap activates.
    a_list: list[int] = []
    b_list: list[int] = []
    n_classes_used = 0

    for key, members in classes.items():
        if len(members) < 2:
            continue
        n_classes_used += 1
        n = len(members)
        cap = min(max_per_class, max(1, int(n * math.log2(max(n, 2)))))
        all_pairs = list(itertools.combinations(members, 2))
        if len(all_pairs) > cap:
            random.shuffle(all_pairs)
            all_pairs = all_pairs[:cap]
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

    # 2b. Card metadata (name/type for offline eval on GPU machine)
    card_meta = _load_card_meta(id_to_idx)

    # 3. Synergy pairs (Phase 2) — XMage class-name edges only.
    #    commander_value edges are excluded here; they belong in the commanders
    #    artifact produced by export_dataset_commanders.py.
    a_idx, b_idx, labels = _load_synergy_pairs(
        id_to_idx, normed,
        ability_score_type="xmage_ability_trigger",
        include_effect_peer=True,
    )

    # 4. Assemble and save
    #    Phases 3/4 are handled by the commanders artifact (mtg_commanders.pt).
    meta = {
        "model":                  os.environ.get("EMBEDDING_MODEL", "sentence-transformers/all-mpnet-base-v2"),
        "dim":                    int(emb_matrix.shape[1]),
        "card_count":             n,
        "functional_pair_count":  int(len(fp_a)),
        "synergy_count":          int(len(a_idx)),
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
        "card_meta": card_meta,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    log.info("Saving artifact → %s", OUTPUT_PATH)
    torch.save(artifact, OUTPUT_PATH)

    meta_path = OUTPUT_PATH.with_suffix(".json")
    meta_path.write_text(json.dumps(meta, indent=2))
    log.info("Metadata sidecar → %s", meta_path)

    size_mb = OUTPUT_PATH.stat().st_size / 1e6
    log.info(
        "Done. %.1f MB  |  %d cards  |  %d functional pairs  |  %d synergy pairs",
        size_mb, n, len(fp_a), len(a_idx),
    )


if __name__ == "__main__":
    main()
