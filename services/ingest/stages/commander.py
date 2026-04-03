"""Compute decomposed_candidates synergy edges for all legal commanders.

For each commander that has card_abilities rows written by ``decompose_commanders``
(source='decompose'), this stage:

  1. Reads trigger_event (pattern) keys from card_abilities WHERE source='decompose'.
  2. For each unique key, executes PRODUCER + CONSUMER SQL from commander_mechanics.py
     to find matching candidate cards.
  3. Applies color-identity filter: card_ci ⊆ commander_ci.
  4. Writes (commander, card) pairs to synergy_edges as score_type='decomposed_candidates'.

Existing decomposed_candidates rows are deleted and rebuilt on each run.
Safe to re-run; prerequisite: pipeline.py --stage decompose_commanders.

Usage
-----
    docker compose run --rm ingest python pipeline.py --stage compute_commander_value_synergy
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from pathlib import Path

import psycopg2
import psycopg2.extras

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from synergy.commander_mechanics import (
    PATTERN_KEY_TO_CONSUMER_SQL,
    PATTERN_KEY_TO_PRODUCER_SQL,
    PRODUCER_DECOMPOSE_TO_DECK_KEY,
)

log = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "").replace(
    "postgresql+asyncpg://", "postgresql://"
)

_UPSERT = """
    INSERT INTO synergy_edges (card_a, card_b, score_type, score, metadata)
    VALUES (%(card_a)s::uuid, %(card_b)s::uuid, 'decomposed_candidates', %(score)s, %(metadata)s::jsonb)
    ON CONFLICT (card_a, card_b, score_type) DO UPDATE SET
        score    = EXCLUDED.score,
        metadata = EXCLUDED.metadata
"""


def compute_commander_value_synergy() -> None:
    """Rebuild synergy_edges rows with score_type='decomposed_candidates'.

    Reads decompose pattern keys from card_abilities, runs the SQL from
    commander_mechanics.py for each key, applies color-identity legality,
    and bulk-inserts the resulting (commander, candidate_card) pairs.
    """
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is required.")

    conn = psycopg2.connect(DATABASE_URL)
    try:
        # ── 1. Color identities for every card ───────────────────────────────
        with conn.cursor() as cur:
            cur.execute("SELECT id::text, color_identity FROM cards")
            color_ids: dict[str, frozenset] = {
                row[0]: frozenset(row[1] or []) for row in cur.fetchall()
            }
        log.info("Loaded color identities for %d cards", len(color_ids))

        # ── 2. Pattern keys per commander (source='decompose') ────────────────
        cmd_patterns: dict[str, list[str]] = defaultdict(list)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT card_id::text, trigger_event
                FROM card_abilities
                WHERE source = 'decompose'
                  AND trigger_event IS NOT NULL
                """
            )
            for card_id, trigger_event in cur.fetchall():
                cmd_patterns[card_id].append(trigger_event)

        if not cmd_patterns:
            log.warning(
                "No source='decompose' rows found in card_abilities — "
                "run 'pipeline.py --stage decompose_commanders' first."
            )
            return

        log.info(
            "Loaded decompose signals for %d commanders", len(cmd_patterns)
        )

        # ── 3. Execute SQL once per unique pattern key ────────────────────────
        all_keys: set[str] = {k for keys in cmd_patterns.values() for k in keys}
        key_cards: dict[str, set[str]] = {}

        for key in sorted(all_keys):
            where_clauses: list[str] = []
            # Producer: decompose key → deck keys → SQL
            for deck_key in PRODUCER_DECOMPOSE_TO_DECK_KEY.get(key, []):
                if deck_key in PATTERN_KEY_TO_PRODUCER_SQL:
                    where_clauses.append(PATTERN_KEY_TO_PRODUCER_SQL[deck_key])
            # Consumer: decompose key == deck key
            if key in PATTERN_KEY_TO_CONSUMER_SQL:
                where_clauses.append(PATTERN_KEY_TO_CONSUMER_SQL[key])
            if not where_clauses:
                log.debug("  key=%-35s  no SQL entry — skipped", key)
                continue

            cards_for_key: set[str] = set()
            for where in where_clauses:
                with conn.cursor() as cur:
                    cur.execute(f"SELECT id::text FROM cards WHERE {where}")
                    cards_for_key.update(row[0] for row in cur.fetchall())

            key_cards[key] = cards_for_key
            log.debug("  key=%-35s  %d cards", key, len(cards_for_key))

        log.info(
            "Executed SQL for %d / %d unique pattern keys",
            len(key_cards),
            len(all_keys),
        )

        # ── 4. Delete stale edges ─────────────────────────────────────────────
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM synergy_edges WHERE score_type = 'decomposed_candidates'"
            )
        log.info("Deleted existing decomposed_candidates edges")

        # ── 5. Build (commander, card) pairs with color filter ────────────────
        rows: list[dict] = []
        for cmd_id, keys in cmd_patterns.items():
            cmd_ci = color_ids.get(cmd_id, frozenset())
            # Accumulate matched pattern keys per candidate card
            card_keys: dict[str, list[str]] = defaultdict(list)
            for key in keys:
                if key not in key_cards:
                    continue
                for card_id in key_cards[key]:
                    if card_id == cmd_id:
                        continue
                    if color_ids.get(card_id, frozenset()) <= cmd_ci:
                        card_keys[card_id].append(key)

            for card_id, matched_keys in card_keys.items():
                rows.append(
                    {
                        "card_a": cmd_id,
                        "card_b": card_id,
                        "score": 1.0,
                        "metadata": json.dumps(
                            {"pattern_keys": sorted(set(matched_keys))}
                        ),
                    }
                )

        log.info("Built %d (commander, card) candidate pairs", len(rows))

        # ── 6. Bulk insert ────────────────────────────────────────────────────
        if rows:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(cur, _UPSERT, rows, page_size=1000)
            conn.commit()
            log.info(
                "Inserted %d decomposed_candidates edges across %d commanders",
                len(rows),
                len({r["card_a"] for r in rows}),
            )
        else:
            log.warning("No edges generated — check pattern key coverage in commander_mechanics.py")

    finally:
        conn.close()
