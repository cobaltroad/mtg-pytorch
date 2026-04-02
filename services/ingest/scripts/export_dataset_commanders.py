"""
Export a commander-centric training artifact from synergy edges.

Produces /data/mtg_commanders.pt — a self-contained artifact for Phase 3
BPR training that does NOT require human decklists.

Background
----------
Human decklists introduce representation collapse in BPR training because
every commander needs the same generic roles (draw, ramp, removal), driving
all commander embeddings toward an indistinct high-similarity cluster.

This artifact builds per-commander positive sets by reading pattern keys from
``card_abilities`` rows written by ``pipeline.py --stage decompose_commanders``
(source='decompose'), then executing SQL from commander_mechanics.py to find
the relevant producer/consumer cards.

  PRODUCER keys  commander *needs* these cards (e.g. tribal_elf → Elf creatures,
                 mana_dork → mana-ability creatures for Tyvar)
  CONSUMER keys  commander *outputs* something these cards amplify (e.g.
                 attack_trigger → attack payoffs for an attack-trigger commander)

Because each commander decomposes into a distinct set of pattern keys, the
positive sets are genuinely distinct across commanders, giving BPR a
meaningful gradient.

Prerequisite
------------
Run ``pipeline.py --stage decompose_commanders`` before this script.
Pattern detection lives exclusively in ``stages/decompose.py``; this script
reads the DB output so the UI and training artifact are always consistent.

Data flow
---------
1. Load embeddings + card metadata from the database.
2. Load all embedded legal commanders.
3. Read pattern keys from card_abilities WHERE source='decompose':
   a. Group trigger_event values by commander card_id.
   b. Execute PRODUCER / CONSUMER SQL once per unique key (cached).
   c. Apply color-identity filter: card_ci ⊆ commander_ci.
4. Per commander:
   a. Skip commanders with < MIN_POSITIVES cards.
   b. Cap positives at MAX_POSITIVES (shuffle + truncate).
   c. Build legal_neg_indices: color-legal embedded cards NOT in positive set.
   d. Archetype derived from matched pattern keys.
5. Save artifact as mtg_commanders.pt.

Artifact keys
-------------
  meta               – provenance: model, dim, counts, created_at
  card_ids           – list[str], N UUIDs in index order
  embeddings         – Tensor(N, 768) float32
  card_meta          – {card_id: {name, mana_cost, type_line}}
  color_identities   – {card_id: list[str]}
  decks              – list[dict], one per commander:
                         commander_idx     int
                         card_idxs         list[int]   (positive producers)
                         color_identity    list[str]
                         legal_neg_indices Tensor[int64]
                         archetype         str  (distinct trigger_event values)
  synergy_positions  – list[dict], one per (commander, positive-card) pair:
                         commander_idx     int
                         context_card_idxs list[int]   (empty — pairwise signal)
                         target_card_idx   int
                         weight            float        (2.0 = ability_weight)
                         legal_neg_indices Tensor[int64]
                       Required by Phase 4 train_deck_constructor_phase for
                       synergy-guided scoring steps (--syn-per-epoch).

Usage
-----
    docker compose run --rm ingest python export_dataset_commanders.py

Environment variables
---------------------
  COMMANDERS_OUTPUT  Output .pt path       (default: /data/mtg_commanders.pt)
  COMMANDERS_MIN_POS Min producers to include a commander   (default: 10)
  COMMANDERS_MAX_POS Cap per-commander positives            (default: 300)
"""

from __future__ import annotations


import json
import logging
import os
import random
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import psycopg2
import psycopg2.extras
import torch

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from export_db_helpers import (
    EMBEDDING_MODEL,
    _load_embeddings,
    _load_card_meta,
    _load_color_identities,
    get_conn,
)
from synergy.commander_mechanics import (
    PATTERN_KEY_TO_PRODUCER_SQL,
    PATTERN_KEY_TO_CONSUMER_SQL,
    PRODUCER_DECOMPOSE_TO_DECK_KEY,
)
from synergy.staples import STAPLE_CATEGORIES
from mtg_sql import commanders

OUTPUT_PATH = Path(os.environ.get("COMMANDERS_OUTPUT", "/data/mtg_commanders.pt"))
MIN_POSITIVES = int(os.environ.get("COMMANDERS_MIN_POS", "10"))
MAX_POSITIVES = int(os.environ.get("COMMANDERS_MAX_POS", "300"))

try:
    GIT_COMMIT = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    log.info("git_commit: %s", GIT_COMMIT)
except (FileNotFoundError, subprocess.CalledProcessError):
    GIT_COMMIT = os.environ.get("GIT_COMMIT", "")
    if not GIT_COMMIT:
        log.error(
            "git not available in container and GIT_COMMIT env var is not set. "
            "Re-run with: docker compose run -e GIT_COMMIT=$(git rev-parse HEAD) ..."
        )
        raise SystemExit(1)
    log.info("git_commit (from env): %s", GIT_COMMIT)


# ── Step 4: Legal commanders ──────────────────────────────────────────────────


def _load_commander_ids(id_to_idx: dict[str, int]) -> set[str]:
    """Return all embedded cards that are legal commanders."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT id::text FROM cards WHERE {commanders.WHERE}")
            all_ids = {row[0] for row in cur.fetchall()}
    embedded = all_ids & id_to_idx.keys()
    log.info("Legal commanders: %d total, %d embedded", len(all_ids), len(embedded))
    return embedded


# ── Step 5: Decompose-based positives ────────────────────────────────────────


def _load_commander_positives(
    commander_ids: set[str],
    id_to_idx: dict[str, int],
    color_ids: dict[str, frozenset],
) -> dict[str, tuple[set[str], list[str]]]:
    """Return {commander_id: (pos_card_ids, pattern_keys)} using decompose signals.

    Pattern keys are read from card_abilities rows written by
    ``pipeline.py --stage decompose_commanders`` (source='decompose').
    Run that stage before calling this function.

    For each commander:
    1. Read trigger_event values from card_abilities WHERE source='decompose'.
    2. For each matched key, look up PRODUCER or CONSUMER SQL from
       commander_mechanics.py and execute it once (results are cached by key).
    3. Apply color-identity filter: card_ci ⊆ commander_ci.
    4. Execute each staple category SQL once (cached), then per-commander
       sample min(eligible, round(rate * MAX_POSITIVES)) cards after color
       filter.  Staple keys are NOT added to key_list so the archetype field
       is unaffected.
    """
    result: dict[str, tuple[set[str], list[str]]] = {
        cid: (set(), []) for cid in commander_ids
    }

    # ── Read pattern keys from card_abilities (source='decompose') ────────────
    cmd_list = list(commander_ids)
    cmd_patterns: dict[str, list[str]] = defaultdict(list)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT card_id::text, trigger_event
                FROM card_abilities
                WHERE source = 'decompose'
                  AND trigger_event IS NOT NULL
                  AND card_id::text = ANY(%s)
            """,
                (cmd_list,),
            )
            for card_id, trigger_event in cur.fetchall():
                cmd_patterns[card_id].append(trigger_event)

    zero_signal = [cid for cid in commander_ids if not cmd_patterns.get(cid)]
    if zero_signal:
        log.warning(
            "%d commanders have no source='decompose' rows in card_abilities — "
            "run 'pipeline.py --stage decompose_commanders' first. "
            "These commanders will be skipped.",
            len(zero_signal),
        )

    all_keys: set[str] = {k for keys in cmd_patterns.values() for k in keys}

    # ── Execute SQL for each unique key (cached) ──────────────────────────────
    # A key may appear in PRODUCER, CONSUMER, or both — union the card sets.
    key_cards: dict[str, set[str]] = {}
    with get_conn() as conn:
        for key in sorted(all_keys):
            where_clauses = []
            # Producer: decompose key → list of deck keys → SQL
            for deck_key in PRODUCER_DECOMPOSE_TO_DECK_KEY.get(key, []):
                if deck_key in PATTERN_KEY_TO_PRODUCER_SQL:
                    where_clauses.append(PATTERN_KEY_TO_PRODUCER_SQL[deck_key])
            # Consumer: decompose key == deck key
            if key in PATTERN_KEY_TO_CONSUMER_SQL:
                where_clauses.append(PATTERN_KEY_TO_CONSUMER_SQL[key])
            if not where_clauses:
                continue

            cards_for_key: set[str] = set()
            for where in where_clauses:
                with conn.cursor() as cur:
                    cur.execute(f"SELECT id::text FROM cards WHERE {where}")
                    for (card_id,) in cur.fetchall():
                        if card_id in id_to_idx:
                            cards_for_key.add(card_id)
            key_cards[key] = cards_for_key
            log.debug("  key=%-30s  %d cards", key, len(cards_for_key))

    log.info(
        "Executed SQL for %d/%d unique pattern keys", len(key_cards), len(all_keys)
    )

    # ── Execute staple SQL once per category (results cached) ─────────────────
    staple_cards: dict[str, set[str]] = {}
    with get_conn() as conn:
        for category, (where, _) in STAPLE_CATEGORIES.items():
            cards_for_cat: set[str] = set()
            with conn.cursor() as cur:
                cur.execute(f"SELECT id::text FROM cards WHERE {where}")
                for (card_id,) in cur.fetchall():
                    if card_id in id_to_idx:
                        cards_for_cat.add(card_id)
            staple_cards[category] = cards_for_cat
            log.debug("  staple=%-20s  %d eligible", category, len(cards_for_cat))

    log.info(
        "Staple categories: %d categories, %d distinct eligible cards",
        len(staple_cards),
        len({c for s in staple_cards.values() for c in s}),
    )

    # ── Assign positives per commander with color filter ─────────────────────
    for cid in commander_ids:
        keys = cmd_patterns.get(cid, [])
        pos_ids, key_list = result[cid]
        cmd_ci = color_ids.get(cid, frozenset())

        # Mechanic-specific positives (drive archetype / key_list)
        for key in keys:
            if key not in key_cards:
                continue
            for card_id in key_cards[key]:
                card_ci = color_ids.get(card_id, frozenset())
                if card_ci <= cmd_ci:
                    pos_ids.add(card_id)
            key_list.append(key)

        # Staple positives — rate-capped, color-filtered, not added to key_list
        for category, (_, rate) in STAPLE_CATEGORIES.items():
            eligible = [
                card_id
                for card_id in staple_cards[category]
                if color_ids.get(card_id, frozenset()) <= cmd_ci
            ]
            n_take = round(rate * MAX_POSITIVES)
            if len(eligible) > n_take:
                random.shuffle(eligible)
                eligible = eligible[:n_take]
            pos_ids.update(eligible)

    total_pos = sum(len(v[0]) for v in result.values())
    covered = sum(1 for v in result.values() if v[0])
    log.info(
        "Positives loaded: %d commanders with ≥1 positive, %d total cards",
        covered,
        total_pos,
    )
    return result


# ── Step 6: Build synthetic decks ─────────────────────────────────────────────


def _build_commander_decks(
    commander_ids: set[str],
    id_to_idx: dict[str, int],
    card_ids: list[str],
    color_ids: dict[str, frozenset],
    positives: dict[str, tuple[set[str], list[str]]],
) -> list[dict]:
    n = len(card_ids)
    _legal_cache: dict[frozenset, np.ndarray] = {}

    def _legal_indices(cmd_ci: frozenset) -> np.ndarray:
        if cmd_ci not in _legal_cache:
            idx = np.array(
                [
                    i
                    for i, cid in enumerate(card_ids)
                    if color_ids.get(cid, frozenset()) <= cmd_ci
                ],
                dtype=np.int64,
            )
            _legal_cache[cmd_ci] = idx if len(idx) > 0 else np.arange(n, dtype=np.int64)
        return _legal_cache[cmd_ci]

    decks: list[dict] = []
    skipped_few = 0

    for commander_id in sorted(commander_ids):
        pos_ids, trigger_events = positives.get(commander_id, (set(), []))
        pos_ids.discard(commander_id)

        if len(pos_ids) < MIN_POSITIVES:
            skipped_few += 1
            continue

        pos_list = list(pos_ids)
        if len(pos_list) > MAX_POSITIVES:
            random.shuffle(pos_list)
            pos_list = pos_list[:MAX_POSITIVES]

        pos_idxs = [id_to_idx[pid] for pid in pos_list if pid in id_to_idx]
        cmd_ci = color_ids.get(commander_id, frozenset())
        legal = _legal_indices(cmd_ci)
        pos_idx_set = set(pos_idxs)
        cmd_idx = id_to_idx[commander_id]

        neg_legal = np.array(
            [i for i in legal if i not in pos_idx_set and i != cmd_idx],
            dtype=np.int64,
        )
        if len(neg_legal) == 0:
            neg_legal = legal

        # Archetype: deduplicated trigger_event labels, most frequent first
        event_counts: dict[str, int] = defaultdict(int)
        for e in trigger_events:
            event_counts[e] += 1
        archetype = ", ".join(
            k for k, _ in sorted(event_counts.items(), key=lambda x: -x[1])[:5]
        )

        decks.append(
            {
                "commander_idx": cmd_idx,
                "card_idxs": pos_idxs,
                "color_identity": sorted(cmd_ci),
                "legal_neg_indices": torch.from_numpy(neg_legal),
                "archetype": archetype,
            }
        )

    log.info(
        "Built %d synthetic decks  (skipped %d with < %d positives)",
        len(decks),
        skipped_few,
        MIN_POSITIVES,
    )
    return decks


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    # 1. Embeddings
    card_ids, emb_matrix = _load_embeddings()
    id_to_idx = {cid: i for i, cid in enumerate(card_ids)}

    # 2. Card metadata + color identities
    card_meta = _load_card_meta(id_to_idx)
    color_ids = _load_color_identities(id_to_idx)

    # 3. Legal commanders (embedded subset)
    commander_ids = _load_commander_ids(id_to_idx)

    # 4. Positives from decompose patterns
    positives = _load_commander_positives(commander_ids, id_to_idx, color_ids)

    # 5. Build synthetic decks
    decks = _build_commander_decks(
        commander_ids,
        id_to_idx,
        card_ids,
        color_ids,
        positives,
    )

    if not decks:
        raise RuntimeError(
            "No synthetic decks were built. "
            "Ensure commander_mechanics.py has SQL entries for the detected pattern keys."
        )

    # 6. Build synergy_positions for Phase 4 decoder training.
    #    Each positive card in each synthetic deck becomes one training position.
    #    context_card_idxs is empty (pairwise signal, not sequential).
    #    legal_neg_indices is inherited from the deck (same color-identity pool).
    ABILITY_WEIGHT = 2.0
    synergy_positions = []
    for deck in decks:
        cmd_idx = deck["commander_idx"]
        legal = deck["legal_neg_indices"]
        for card_idx in deck["card_idxs"]:
            synergy_positions.append(
                {
                    "commander_idx": cmd_idx,
                    "context_card_idxs": [],
                    "target_card_idx": card_idx,
                    "weight": ABILITY_WEIGHT,
                    "legal_neg_indices": legal,
                }
            )
    log.info(
        "Built %d synergy_positions from %d decks", len(synergy_positions), len(decks)
    )

    # 7. Assemble and save
    commander_count = len(decks)
    avg_pos = sum(len(d["card_idxs"]) for d in decks) / max(commander_count, 1)
    meta = {
        "model": EMBEDDING_MODEL,
        "dim": int(emb_matrix.shape[1]),
        "card_count": len(card_ids),
        "deck_count": commander_count,
        "avg_positives": round(avg_pos, 1),
        "min_positives": MIN_POSITIVES,
        "max_positives": MAX_POSITIVES,
        "source": "decompose+staples",
        "synergy_pos_count": len(synergy_positions),
        "git_commit": GIT_COMMIT,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    artifact = {
        "meta": meta,
        "card_ids": card_ids,
        "embeddings": torch.from_numpy(emb_matrix),
        "card_meta": card_meta,
        "color_identities": {cid: sorted(ci) for cid, ci in color_ids.items()},
        "decks": decks,
        "synergy_positions": synergy_positions,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    log.info("Saving artifact → %s", OUTPUT_PATH)
    torch.save(artifact, OUTPUT_PATH)

    meta_path = OUTPUT_PATH.with_suffix(".json")
    meta_path.write_text(json.dumps(meta, indent=2))
    log.info("Metadata sidecar → %s", meta_path)

    size_mb = OUTPUT_PATH.stat().st_size / 1e6
    log.info(
        "Done. %.1f MB  |  %d cards  |  %d commanders  |  avg %.0f producers/commander",
        size_mb,
        len(card_ids),
        commander_count,
        avg_pos,
    )


if __name__ == "__main__":
    main()
