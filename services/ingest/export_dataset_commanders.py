"""
Export a commander-centric training artifact from pattern decomposition.

Produces /data/mtg_commanders.pt — a self-contained artifact for Phase 3
training that does NOT require human decklists.

Background
----------
Human decklists introduce representation collapse in BPR training because
every commander needs the same generic roles (draw, ramp, removal), driving
all commander embeddings toward an indistinct high-similarity cluster.

This artifact takes a different approach: for each commander we select
*producer* cards — cards specifically enabled by that commander's pattern
signals (ETB triggers want ETB creatures; enchantment cast triggers want
enchantment spells, etc.).  The result is a per-commander positive set that
is genuinely distinct from other commanders' positive sets, giving BPR a
meaningful gradient to train on.

Data flow
---------
1. Load embeddings + card metadata from the database.
2. Load ``commander_decomposition.json`` — the output of
   ``scripts/decompose_commanders.py``, containing per-commander signals.
3. Pre-build producer cache: one SQL query per pattern key from
   ``synergy/commander_patterns.py:PATTERN_KEY_TO_PRODUCER_SQL``.
   Result: ``{pattern_key: set[card_id]}``.
4. Per commander:
   a. Collect positive card IDs = union of producer sets for each detected
      signal's pattern_key, filtered to cards legal in the commander's
      color identity.
   b. Skip commanders with < MIN_POSITIVES producers.
   c. Cap positives at MAX_POSITIVES (shuffle + truncate).
   d. Build ``legal_neg_indices``: all color-legal embedded cards NOT in the
      positive set.
   e. Emit a deck dict compatible with ``train.py``'s ``DeckDataset``.
5. Save artifact as ``mtg_commanders.pt``.

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
                         archetype         str          (joined pattern keys)

Usage
-----
    docker compose run --rm ingest python export_dataset_commanders.py

    # Custom decomposition file or output path:
    COMMANDERS_INPUT=/data/commander_decomposition.json \\
    COMMANDERS_OUTPUT=/data/mtg_commanders.pt \\
        docker compose run --rm ingest python export_dataset_commanders.py

Environment variables
---------------------
  COMMANDERS_INPUT   Path to commander_decomposition.json
                     (default: /data/commander_decomposition.json)
  COMMANDERS_OUTPUT  Output .pt path
                     (default: /data/mtg_commanders.pt)
  COMMANDERS_MIN_POS Minimum producers required to include a commander (default 10)
  COMMANDERS_MAX_POS Maximum producers per commander — capped for balance (default 300)
"""

from __future__ import annotations

import json
import logging
import os
import random
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import psycopg2
import psycopg2.extras
import torch

from synergy.commander_patterns import PATTERN_KEY_TO_PRODUCER_SQL

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DATABASE_URL    = os.environ["DATABASE_URL"]
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "sentence-transformers/all-mpnet-base-v2")
INPUT_PATH      = Path(os.environ.get("COMMANDERS_INPUT",  "/data/commander_decomposition.json"))
OUTPUT_PATH     = Path(os.environ.get("COMMANDERS_OUTPUT", "/data/mtg_commanders.pt"))
MIN_POSITIVES   = int(os.environ.get("COMMANDERS_MIN_POS", "10"))
MAX_POSITIVES   = int(os.environ.get("COMMANDERS_MAX_POS", "300"))


def _sync_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


def get_conn():
    return psycopg2.connect(_sync_dsn(DATABASE_URL))


# ── Step 1: Embeddings ────────────────────────────────────────────────────────

def _load_embeddings() -> tuple[list[str], np.ndarray]:
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


# ── Step 2: Card metadata ─────────────────────────────────────────────────────

def _load_card_meta(id_to_idx: dict[str, int]) -> dict[str, dict]:
    ids = list(id_to_idx.keys())
    result: dict[str, dict] = {}
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                "SELECT id::text, name, mana_cost, type_line FROM cards WHERE id::text = ANY(%s)",
                (ids,),
            )
            for row in cur.fetchall():
                result[row["id"]] = {
                    "name":      row["name"],
                    "mana_cost": row["mana_cost"] or "",
                    "type_line": row["type_line"] or "",
                }
    return result


# ── Step 3: Color identities ──────────────────────────────────────────────────

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


# ── Step 4: Producer cache ────────────────────────────────────────────────────

def _build_producer_cache(
    id_to_idx: dict[str, int],
    pattern_keys: set[str],
) -> dict[str, set[str]]:
    """Pre-build {pattern_key: set[card_id]} with one query per key.

    Only queries keys that are actually present in the decomposition data,
    avoiding unnecessary round-trips for unused patterns.
    """
    log.info("Building producer cache for %d pattern keys…", len(pattern_keys))
    cache: dict[str, set[str]] = {}

    with get_conn() as conn:
        with conn.cursor() as cur:
            for key in sorted(pattern_keys):
                sql_where = PATTERN_KEY_TO_PRODUCER_SQL.get(key)
                if sql_where is None:
                    log.warning("  No producer SQL for pattern key %r — skipping", key)
                    cache[key] = set()
                    continue

                cur.execute(
                    f"SELECT id::text FROM cards WHERE {sql_where}",
                )
                ids = {row[0] for row in cur.fetchall() if row[0] in id_to_idx}
                cache[key] = ids
                log.info("  %-35s  %6d producer cards", key, len(ids))

    total = sum(len(v) for v in cache.values())
    log.info("Producer cache complete: %d keys, %d total entries", len(cache), total)
    return cache


# ── Step 5: Build synthetic decks ─────────────────────────────────────────────

def _build_commander_decks(
    decomposition: list[dict],
    id_to_idx: dict[str, int],
    card_ids: list[str],
    color_ids: dict[str, frozenset],
    producer_cache: dict[str, set[str]],
) -> list[dict]:
    """Build one synthetic deck record per commander.

    For each commander:
    - positives  = union of producer sets for all detected signal pattern keys,
                   filtered to cards that fit the commander's color identity.
    - negatives  = all color-legal embedded cards NOT in the positive set.
    """
    log.info("Building synthetic decks for %d commanders…", len(decomposition))

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

    decks: list[dict] = []
    skipped_no_id   = 0
    skipped_no_sig  = 0
    skipped_few_pos = 0

    for entry in decomposition:
        signals = entry.get("signals", [])
        if not signals:
            skipped_no_sig += 1
            continue

        # Decomposition entries have an "id" field — the card UUID from the DB.
        commander_id: str | None = entry.get("id")
        if commander_id is None or commander_id not in id_to_idx:
            skipped_no_id += 1
            continue

        cmd_idx = id_to_idx[commander_id]
        cmd_ci  = color_ids.get(commander_id, frozenset())

        # Collect positives: union of producers for each signal, color-filtered
        pos_ids: set[str] = set()
        pattern_keys_used: list[str] = []
        for sig in signals:
            key = sig.get("pattern_key", "")
            producers = producer_cache.get(key, set())
            # Color-identity filter
            color_legal = {pid for pid in producers
                           if color_ids.get(pid, frozenset()) <= cmd_ci}
            if color_legal:
                pos_ids.update(color_legal)
                pattern_keys_used.append(key)

        # Remove the commander itself from positives
        pos_ids.discard(commander_id)

        if len(pos_ids) < MIN_POSITIVES:
            skipped_few_pos += 1
            continue

        # Cap positives
        pos_list = list(pos_ids)
        if len(pos_list) > MAX_POSITIVES:
            random.shuffle(pos_list)
            pos_list = pos_list[:MAX_POSITIVES]

        pos_idxs = [id_to_idx[pid] for pid in pos_list if pid in id_to_idx]

        # Legal negatives: color-legal cards not in positive set
        legal = _legal_indices(cmd_ci)
        pos_idx_set = set(pos_idxs)
        neg_legal = np.array(
            [i for i in legal if i not in pos_idx_set and i != cmd_idx],
            dtype=np.int64,
        )
        if len(neg_legal) == 0:
            neg_legal = legal

        archetype = ", ".join(dict.fromkeys(pattern_keys_used))  # deduplicated, ordered

        decks.append({
            "commander_idx":     cmd_idx,
            "card_idxs":         pos_idxs,
            "color_identity":    sorted(cmd_ci),
            "legal_neg_indices": torch.from_numpy(neg_legal),
            "archetype":         archetype,
        })

    log.info(
        "Built %d synthetic decks  "
        "(skipped: %d no_id, %d no_signals, %d too_few_producers)",
        len(decks), skipped_no_id, skipped_no_sig, skipped_few_pos,
    )
    return decks


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if not INPUT_PATH.exists():
        raise FileNotFoundError(
            f"Commander decomposition not found: {INPUT_PATH}\n"
            "Run: docker compose run --rm ingest python scripts/decompose_commanders.py"
        )

    log.info("Loading commander decomposition from %s…", INPUT_PATH)
    with INPUT_PATH.open(encoding="utf-8") as fh:
        decomposition: list[dict] = json.load(fh)
    log.info("Loaded %d commander entries", len(decomposition))

    # 1. Embeddings
    card_ids, emb_matrix = _load_embeddings()
    id_to_idx = {cid: i for i, cid in enumerate(card_ids)}

    # 2. Card metadata
    card_meta = _load_card_meta(id_to_idx)

    # 3. Color identities
    color_ids = _load_color_identities(id_to_idx)

    # 4. Producer cache (only for keys actually present in the decomposition)
    all_pattern_keys: set[str] = set()
    for entry in decomposition:
        for sig in entry.get("signals", []):
            k = sig.get("pattern_key")
            if k:
                all_pattern_keys.add(k)
    producer_cache = _build_producer_cache(id_to_idx, all_pattern_keys)

    # 5. Build synthetic decks
    decks = _build_commander_decks(
        decomposition, id_to_idx, card_ids, color_ids, producer_cache,
    )

    if not decks:
        raise RuntimeError(
            "No synthetic decks were built. "
            "Check that the decomposition JSON has 'card_id' fields "
            "and that the embeddings are populated."
        )

    # 6. Assemble and save
    n                 = len(card_ids)
    commander_count   = len(decks)
    avg_pos           = sum(len(d["card_idxs"]) for d in decks) / max(commander_count, 1)
    meta = {
        "model":           EMBEDDING_MODEL,
        "dim":             int(emb_matrix.shape[1]),
        "card_count":      n,
        "deck_count":      commander_count,
        "avg_positives":   round(avg_pos, 1),
        "min_positives":   MIN_POSITIVES,
        "max_positives":   MAX_POSITIVES,
        "source":          "commander_decomposition",
        "decomposition":   str(INPUT_PATH),
        "created_at":      datetime.now(timezone.utc).isoformat(),
    }

    artifact = {
        "meta":             meta,
        "card_ids":         card_ids,
        "embeddings":       torch.from_numpy(emb_matrix),
        "card_meta":        card_meta,
        "color_identities": {cid: sorted(ci) for cid, ci in color_ids.items()},
        "decks":            decks,
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
        size_mb, n, commander_count, avg_pos,
    )


if __name__ == "__main__":
    main()
