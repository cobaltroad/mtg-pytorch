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
  synergy                 – a_idx / b_idx / labels (Phase 2; xmage_ability_trigger + combo + negatives)
  effect_peer             – a_idx / b_idx int32 tensors (Phase 2; direct peer pairs by trigger_event/effect_class)
  card_meta               – {card_id: {name, mana_cost, type_line, cmc, color_identity}} for offline eval

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
  DATASET_COMBO_SAMPLE   Max combo pair positives          (default 200 000)
  DATASET_NEG_RATIO      Negatives per positive            (default 3)
  DATASET_HARD_NEG_FRAC  Fraction of negs that are hard   (default 0.5)
  DATASET_SYN_LIMIT      Max synergy positions/commander   (default 300)
"""

from __future__ import annotations

import csv
import itertools
import json
import hashlib
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
    _load_effect_peer_pairs,
    _load_synergy_pairs,
    get_conn,
)

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

OUTPUT_PATH              = Path(os.environ.get("DATASET_OUTPUT", "/data/mtg_dataset.pt"))
MAX_PER_CLASS            = int(os.environ.get("COMP_MAX_PER_CLASS", "500"))
STAPLE_MAX_PER_CATEGORY  = int(os.environ.get("STAPLE_MAX_PER_CATEGORY", "2000"))
_MANA_ARTIFACTS_CSV      = Path(os.environ.get("MANA_ARTIFACTS_CSV", "/app/edhrec/mana-artifacts.csv"))


def _type_bucket(type_line: str) -> str:
    """Map a card's type_line to a coarse super-type bucket.

    Functional equivalence is only meaningful within the same bucket — a Land
    and a Creature cannot substitute for each other in a deck slot.

    Buckets:
      land             – any card with Land in its type line
      instant_sorcery  – Instant or Sorcery (non-permanent spells)
      nonland_permanent – everything else (Creature, Artifact, Enchantment,
                          Planeswalker, Battle …)
    """
    tl = type_line.lower()
    if "land" in tl:
        return "land"
    if "instant" in tl or "sorcery" in tl:
        return "instant_sorcery"
    return "nonland_permanent"


def _build_functional_pairs(
    card_ids: list[str],
    id_to_idx: dict[str, int],
    max_per_class: int = MAX_PER_CLASS,
) -> tuple[np.ndarray, np.ndarray]:
    """Build Phase 1 functional equivalence pairs from card_abilities.

    Two cards are placed in the same equivalence class when they share the
    same ability role (e.g. 'removal', 'ramp', 'repeatable_draw') **and**
    belong to the same super-type bucket (land / instant_sorcery /
    nonland_permanent).  Color identity and CMC are intentionally excluded —
    functional equivalence is about what a card does for the caster, not what
    it costs or what color it is.  Deckbuilding constraints are applied
    downstream.

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
                    COALESCE(ca.effect_class, '') AS effect_class,
                    COALESCE(c.type_line, '')     AS type_line
                FROM card_abilities ca
                JOIN cards c ON c.id = ca.card_id
                WHERE ca.ability_type = 'role'
            """)
            rows = cur.fetchall()

    # Group card indices by (role, effect_class, type_bucket).
    # Cards with a non-null effect_class land in the precise subtype class.
    # Legacy rows with effect_class='' land in a coarse catch-all per role,
    # which will shrink to zero once tag_abilities --rescan has been run.
    # The type_bucket prevents cross-type pairings (e.g. Land ↔ Creature).
    classes: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    seen_per_class: dict[tuple[str, str, str], set[int]] = defaultdict(set)
    for row in rows:
        card_id = row["card_id"]
        if card_id not in embedded:
            continue
        idx = id_to_idx[card_id]
        key = (row["role"], row["effect_class"], _type_bucket(row["type_line"]))
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


def _cmc_weight(cmc_a: float, cmc_b: float) -> float:
    """Pair weight based on CMC proximity.

    Same CMC → 1.0 (full signal).  3+ CMC apart → 0.5 (soft signal; cards
    probably occupy different deck slots even if they share a role).
    """
    delta = abs(cmc_a - cmc_b)
    if delta == 0:
        return 1.0
    elif delta <= 1:
        return 0.85
    elif delta <= 2:
        return 0.70
    return 0.50


def _build_staple_pairs(
    card_ids: list[str],
    id_to_idx: dict[str, int],
    card_meta: dict,
    max_per_category: int = STAPLE_MAX_PER_CATEGORY,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build Phase 1 soft-positive pairs from EDHREC staple categories.

    Two sources contribute:
    1. mana_rocks — curated list from ingest_cache/edhrec/mana_artifacts.csv
       (EDHREC-vetted mana artifacts; more precise than the SQL ramp bucket)
    2. STAPLE_CATEGORIES SQL — non-land categories (removal, sweeper,
       draw_engine, draw_spell, interaction, ramp)

    Pairs within each category are weighted by CMC proximity via _cmc_weight:
    same-CMC pairs → 1.0, 3+-CMC-apart pairs → 0.5.  This prevents the model
    from treating a Sol Ring (CMC 1) and a Gilded Lotus (CMC 5) as perfectly
    interchangeable while still anchoring them in the same role cluster.

    Returns (a_idx, b_idx, weights) as int32/float32 arrays.
    """
    from synergy.staples import STAPLE_CATEGORIES

    embedded = set(id_to_idx.keys())

    # name → card_id lookup from card_meta (first embedded match wins).
    # Index both the full name and the front-face name so that cards stored
    # with MTGJSON's "Name // Name" double-suffix (e.g. "Sol Ring // Sol Ring")
    # are still matched by their plain CSV name ("Sol Ring").
    name_to_id: dict[str, str] = {}
    for cid, meta in card_meta.items():
        name = meta.get("name", "")
        if not name:
            continue
        if name not in name_to_id:
            name_to_id[name] = cid
        if " // " in name:
            front = name.split(" // ")[0].strip()
            if front and front not in name_to_id:
                name_to_id[front] = cid

    def _cmc(cid: str) -> float:
        return float(card_meta.get(cid, {}).get("cmc") or 0.0)

    categories: dict[str, list[str]] = {}

    # 1. mana_rocks from EDHREC CSV (high-precision mana-artifact category)
    if _MANA_ARTIFACTS_CSV.exists():
        rock_ids: list[str] = []
        with open(_MANA_ARTIFACTS_CSV, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                name = row.get("Name", "").strip()
                if not name or name.lower() == "undefined":
                    continue
                cid = name_to_id.get(name)
                if cid and cid in embedded:
                    rock_ids.append(cid)
        if rock_ids:
            categories["mana_rocks"] = rock_ids
            log.info("Staple pairs: mana_rocks from CSV → %d cards", len(rock_ids))
    else:
        log.warning("mana_artifacts CSV not found at %s — skipping mana_rocks category", _MANA_ARTIFACTS_CSV)

    # 2. STAPLE_CATEGORIES SQL — skip land categories (too universal)
    with get_conn() as conn:
        with conn.cursor() as cur:
            for category, (where, _rate) in STAPLE_CATEGORIES.items():
                if category in ("manabase", "utilityland"):
                    continue
                cur.execute(f"SELECT id::text FROM cards WHERE {where}")
                ids = [r[0] for r in cur.fetchall() if r[0] in embedded]
                if ids:
                    categories[category] = ids
                    log.info("Staple pairs: %s → %d cards", category, len(ids))

    # Generate CMC-weighted pairs with a proportional cap per category
    a_list: list[int] = []
    b_list: list[int] = []
    w_list: list[float] = []
    rng = random.Random(42)

    for cat_name, members in categories.items():
        if len(members) < 2:
            continue
        n = len(members)
        cap = min(max_per_category, max(1, int(n * math.log2(max(n, 2)))))
        all_pairs = list(itertools.combinations(members, 2))
        if len(all_pairs) > cap:
            rng.shuffle(all_pairs)
            all_pairs = all_pairs[:cap]
        for a, b in all_pairs:
            a_list.append(id_to_idx[a])
            b_list.append(id_to_idx[b])
            w_list.append(_cmc_weight(_cmc(a), _cmc(b)))

    log.info(
        "Staple pairs: %d total across %d categories",
        len(a_list), len(categories),
    )
    return (
        np.array(a_list, dtype=np.int32),
        np.array(b_list, dtype=np.int32),
        np.array(w_list, dtype=np.float32),
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

    # 2c. Staple role pairs (Phase 1 soft positives) — CMC-weighted pairs within
    #     each EDHREC staple category (mana_rocks, removal, sweeper, etc.).
    #     Lower weight than oracle-identity pairs; used to bootstrap role geometry.
    sp_a, sp_b, sp_w = _build_staple_pairs(card_ids, id_to_idx, card_meta)

    # 3. Synergy pairs (Phase 2) — XMage class-name edges + combo.
    #    effect_peer is stored separately (see step 3b) so the trainer can use
    #    those pairs directly rather than routing them through producer-grouping.
    #    commander_value edges are excluded here; they belong in the commanders
    #    artifact produced by export_dataset_commanders.py.
    a_idx, b_idx, labels = _load_synergy_pairs(
        id_to_idx, normed,
        ability_score_type="xmage_ability_trigger",
        include_effect_peer=False,
    )

    # 3b. Effect-peer pairs — cards sharing (trigger_event, effect_class).
    #     Stored as a separate artifact key so load_synergy_pairs_from_artifact
    #     can add them as direct positives (bypassing producer-grouping, which
    #     silently discards symmetric peer edges).
    ep_a, ep_b = _load_effect_peer_pairs(id_to_idx)

    # 4. Assemble and save
    #    Phases 3/4 are handled by the commanders artifact (mtg_commanders.pt).
    meta = {
        "model":                  os.environ.get("EMBEDDING_MODEL", "sentence-transformers/all-mpnet-base-v2"),
        "dim":                    int(emb_matrix.shape[1]),
        "card_count":             n,
        "functional_pair_count":  int(len(fp_a)),
        "staple_pair_count":      int(len(sp_a)),
        "synergy_count":          int(len(a_idx)),
        "effect_peer_count":      int(len(ep_a)),
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
        "staple_pairs": {
            "a_idx":   torch.from_numpy(sp_a),
            "b_idx":   torch.from_numpy(sp_b),
            "weights": torch.from_numpy(sp_w),
        },
        "synergy": {
            "a_idx":  torch.from_numpy(a_idx),
            "b_idx":  torch.from_numpy(b_idx),
            "labels": torch.from_numpy(labels),
        },
        "effect_peer": {
            "a_idx": torch.from_numpy(ep_a),
            "b_idx": torch.from_numpy(ep_b),
        },
        "card_meta": card_meta,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    log.info("Saving artifact → %s", OUTPUT_PATH)
    torch.save(artifact, OUTPUT_PATH)

    sha256 = hashlib.sha256(OUTPUT_PATH.read_bytes()).hexdigest()
    meta["sha256"] = sha256
    log.info("SHA256: %s", sha256)

    meta_path = OUTPUT_PATH.with_suffix(".json")
    meta_path.write_text(json.dumps(meta, indent=2))
    log.info("Metadata sidecar → %s", meta_path)

    size_mb = OUTPUT_PATH.stat().st_size / 1e6
    log.info(
        "Done. %.1f MB  |  %d cards  |  %d functional pairs  |  %d staple pairs  "
        "|  %d synergy pairs  |  %d effect_peer pairs",
        size_mb, n, len(fp_a), len(sp_a), len(a_idx), len(ep_a),
    )


if __name__ == "__main__":
    main()
