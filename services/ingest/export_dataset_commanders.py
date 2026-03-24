"""
Export a commander-centric training artifact from synergy edges.

Produces /data/mtg_commanders.pt — a self-contained artifact for Phase 3
BPR training that does NOT require human decklists.

Background
----------
Human decklists introduce representation collapse in BPR training because
every commander needs the same generic roles (draw, ramp, removal), driving
all commander embeddings toward an indistinct high-similarity cluster.

This artifact builds per-commander positive sets from synergy_edges, which
encode distinct mechanic-specific card relationships:

  ability_trigger edges  card_a (producer) → card_b (commander/consumer)
                         "card_a belongs in a deck with this commander
                         because the commander's ability fires when card_a
                         is in play or cast"

  commander_value edges  card_a (commander) → card_b (payoff card)
                         "card_b is better when this commander is in play"

Because producers are selected by what each commander's abilities *react to*
(ETB creatures for an ETB commander, enchantments for Sythis, etc.) the
positive sets are genuinely distinct across commanders, giving BPR a
meaningful gradient.

Data flow
---------
1. Load embeddings + card metadata from the database.
2. Load all embedded legal commanders.
3. Two bulk queries against synergy_edges:
   a. ability_trigger WHERE card_b IN commanders  → producers per commander
   b. commander_value WHERE card_a IN commanders  → payoffs per commander
4. Per commander:
   a. Union ability_trigger producers + commander_value payoffs,
      re-filtered to strict color-identity legality (⊆ commander CI).
   b. Skip commanders with < MIN_POSITIVES cards.
   c. Cap positives at MAX_POSITIVES (shuffle + truncate).
   d. Build legal_neg_indices: color-legal embedded cards NOT in positive set.
   e. Archetype derived from distinct trigger_event values on the edges.
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


# ── Step 4: Legal commanders ──────────────────────────────────────────────────

def _load_commander_ids(id_to_idx: dict[str, int]) -> set[str]:
    """Return all embedded cards that are legal commanders."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id::text FROM cards
                WHERE legalities->>'commander' = 'legal'
                  AND (
                      type_line ILIKE '%Legendary Creature%'
                      OR type_line ILIKE '%Legendary Planeswalker%'
                      OR oracle_text ILIKE '%can be your commander%'
                  )
            """)
            all_ids = {row[0] for row in cur.fetchall()}
    embedded = all_ids & id_to_idx.keys()
    log.info("Legal commanders: %d total, %d embedded", len(all_ids), len(embedded))
    return embedded


# ── Step 5: Synergy edges ─────────────────────────────────────────────────────

def _load_commander_positives(
    commander_ids: set[str],
    id_to_idx: dict[str, int],
    color_ids: dict[str, frozenset],
) -> dict[str, tuple[set[str], list[str]]]:
    """Return {commander_id: (pos_card_ids, trigger_events)} from synergy_edges.

    Sources:
    - ability_trigger: card_a (producer) → card_b (commander)
      Color filtering at edge-build time uses && (overlap), which is looser
      than deck-legality (⊆).  We re-apply strict subset filtering here.
    - commander_value: card_a (commander) → card_b (payoff card)
      Color filtering was intentionally skipped at build time; applied here.
    """
    cmd_list = list(commander_ids)
    # {commander_id: (set of positive card_ids, list of trigger_event labels)}
    result: dict[str, tuple[set[str], list[str]]] = {
        cid: (set(), []) for cid in commander_ids
    }

    with get_conn() as conn:
        with conn.cursor() as cur:

            # ── ability_trigger: producers → commander ────────────────────────
            log.info("Loading ability_trigger edges for %d commanders…", len(cmd_list))
            cur.execute("""
                SELECT card_a::text, card_b::text,
                       COALESCE(metadata->>'trigger_event', '') AS trigger_event
                FROM synergy_edges
                WHERE score_type = 'ability_trigger'
                  AND card_b::text = ANY(%s)
            """, (cmd_list,))
            ability_rows = cur.fetchall()
            log.info("  %d ability_trigger rows fetched", len(ability_rows))

            for card_a, card_b, trigger_event in ability_rows:
                if card_a not in id_to_idx or card_b not in result:
                    continue
                cmd_ci  = color_ids.get(card_b, frozenset())
                card_ci = color_ids.get(card_a, frozenset())
                # Strict subset: producer must fit entirely within commander CI
                if card_ci <= cmd_ci:
                    pos_ids, events = result[card_b]
                    pos_ids.add(card_a)
                    if trigger_event:
                        events.append(trigger_event)

            # ── commander_value: commander → payoff card ──────────────────────
            log.info("Loading commander_value edges for %d commanders…", len(cmd_list))
            cur.execute("""
                SELECT card_a::text, card_b::text
                FROM synergy_edges
                WHERE score_type = 'commander_value'
                  AND card_a::text = ANY(%s)
            """, (cmd_list,))
            cv_rows = cur.fetchall()
            log.info("  %d commander_value rows fetched", len(cv_rows))

            for card_a, card_b in cv_rows:
                if card_a not in result or card_b not in id_to_idx:
                    continue
                cmd_ci  = color_ids.get(card_a, frozenset())
                card_ci = color_ids.get(card_b, frozenset())
                if card_ci <= cmd_ci:
                    pos_ids, events = result[card_a]
                    pos_ids.add(card_b)

    total_pos = sum(len(v[0]) for v in result.values())
    covered   = sum(1 for v in result.values() if v[0])
    log.info("Positives loaded: %d commanders with ≥1 positive, %d total cards",
             covered, total_pos)
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
                [i for i, cid in enumerate(card_ids)
                 if color_ids.get(cid, frozenset()) <= cmd_ci],
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

        pos_idxs   = [id_to_idx[pid] for pid in pos_list if pid in id_to_idx]
        cmd_ci     = color_ids.get(commander_id, frozenset())
        legal      = _legal_indices(cmd_ci)
        pos_idx_set = set(pos_idxs)
        cmd_idx    = id_to_idx[commander_id]

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
        archetype = ", ".join(k for k, _ in sorted(
            event_counts.items(), key=lambda x: -x[1]
        )[:5])

        decks.append({
            "commander_idx":     cmd_idx,
            "card_idxs":         pos_idxs,
            "color_identity":    sorted(cmd_ci),
            "legal_neg_indices": torch.from_numpy(neg_legal),
            "archetype":         archetype,
        })

    log.info(
        "Built %d synthetic decks  (skipped %d with < %d positives)",
        len(decks), skipped_few, MIN_POSITIVES,
    )
    return decks


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    # 1. Embeddings
    card_ids, emb_matrix = _load_embeddings()
    id_to_idx = {cid: i for i, cid in enumerate(card_ids)}

    # 2. Card metadata + color identities
    card_meta  = _load_card_meta(id_to_idx)
    color_ids  = _load_color_identities(id_to_idx)

    # 3. Legal commanders (embedded subset)
    commander_ids = _load_commander_ids(id_to_idx)

    # 4. Positives from synergy_edges (two bulk queries)
    positives = _load_commander_positives(commander_ids, id_to_idx, color_ids)

    # 5. Build synthetic decks
    decks = _build_commander_decks(
        commander_ids, id_to_idx, card_ids, color_ids, positives,
    )

    if not decks:
        raise RuntimeError(
            "No synthetic decks were built. "
            "Ensure compute_synergy and compute_commander_value_synergy have been run."
        )

    # 6. Assemble and save
    commander_count = len(decks)
    avg_pos = sum(len(d["card_idxs"]) for d in decks) / max(commander_count, 1)
    meta = {
        "model":          EMBEDDING_MODEL,
        "dim":            int(emb_matrix.shape[1]),
        "card_count":     len(card_ids),
        "deck_count":     commander_count,
        "avg_positives":  round(avg_pos, 1),
        "min_positives":  MIN_POSITIVES,
        "max_positives":  MAX_POSITIVES,
        "source":         "synergy_edges",
        "created_at":     datetime.now(timezone.utc).isoformat(),
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
        size_mb, len(card_ids), commander_count, avg_pos,
    )


if __name__ == "__main__":
    main()
