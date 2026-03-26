"""Tag stage — embed cards and tag their abilities.

Entrypoint:  python -m stages.tag [--rescan]

Sub-stages:
  embed_cards   — compute sentence-transformer embeddings → card_embeddings
  tag_abilities — oracle-text pattern matching → card_abilities
                  Pass 1:  apply TRIGGER_PATTERNS to new cards
                           (triggered abilities, activated abilities, combat patterns)
                  Pass 1b: gap detection (backfill new trigger_events to all cards)
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from tqdm import tqdm

from stages.db import BATCH_SIZE, EMBEDDING_MODEL, Session

log = logging.getLogger(__name__)

# ── Stage 3: Embed ────────────────────────────────────────────────────────────

from land_tags import annotate_land_oracle  # noqa: E402

from synergy.triggered_ability import TRIGGERED_ABILITY_PATTERNS as _trigger_patterns  # noqa: E402
from synergy.activated_ability import ACTIVATED_ABILITY_PATTERNS as _activated_patterns  # noqa: E402
from synergy.combat import COMBAT_PATTERNS as _combat_patterns  # noqa: E402
from synergy.tribal import TRIBAL_PATTERNS  # noqa: E402

TRIGGER_PATTERNS = [*_trigger_patterns, *_activated_patterns, *_combat_patterns]


def _card_text(row) -> str:
    parts = [row[1]]  # name
    if row[4]:         # type_line
        parts.append(row[4])
    oracle = row[5] or ""
    if oracle:
        # Augment Land oracle text with structured mana-quality tags so the
        # model learns that Verdant Catacombs and Woodland Cemetery cluster
        # together rather than being separated by superficial text differences.
        if row[4] and "Land" in row[4]:
            oracle = annotate_land_oracle(oracle)
        parts.append(oracle)
    return " | ".join(parts)


async def embed_cards() -> None:
    from sentence_transformers import SentenceTransformer

    log.info("Loading embedding model: %s", EMBEDDING_MODEL)
    model = SentenceTransformer(EMBEDDING_MODEL)

    async with Session() as db:
        result = await db.execute(text("""
            SELECT c.id, c.name, c.oracle_id, c.mana_cost, c.type_line, c.oracle_text
            FROM cards c
            WHERE NOT EXISTS (
                SELECT 1 FROM card_embeddings e
                WHERE e.card_id = c.id AND e.model = :model
            )
        """), {"model": EMBEDDING_MODEL})
        rows = result.fetchall()

    log.info("Embedding %d cards (batch=%d)…", len(rows), BATCH_SIZE)
    for i in tqdm(range(0, len(rows), BATCH_SIZE)):
        batch = rows[i : i + BATCH_SIZE]
        texts = [_card_text(r) for r in batch]
        embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)

        async with Session() as db:
            for row, emb in zip(batch, embeddings):
                await db.execute(text("""
                    INSERT INTO card_embeddings (card_id, model, embedding)
                    VALUES (:card_id, :model, :emb)
                    ON CONFLICT (card_id, model) DO NOTHING
                """), {
                    "card_id": str(row[0]),
                    "model": EMBEDDING_MODEL,
                    "emb": "[" + ",".join(str(x) for x in emb.tolist()) + "]",
                })
            await db.commit()


# ── Stage 4: Tag abilities ────────────────────────────────────────────────────

async def tag_abilities(rescan: bool = False) -> None:
    log.info("Tagging abilities%s…", " (full rescan)" if rescan else "")

    # Snapshot which trigger_events already have consumer rows BEFORE Pass 1 so
    # the gap detector doesn't miss events that get their first rows from newly
    # added cards in this very run.
    # With --rescan, treat the existing set as empty so every trigger event is
    # re-applied across all cards (useful after a pattern regex is improved).
    if rescan:
        existing_events: set[str] = set()
    else:
        async with Session() as db:
            existing_events_result = await db.execute(text("""
                SELECT DISTINCT trigger_event FROM card_abilities
                WHERE trigger_event IS NOT NULL
            """))
            existing_events = {row[0] for row in existing_events_result.fetchall()}

    # ── Pass 1: triggered abilities ───────────────────────────────────────────
    # Processes cards that have no ability rows yet (fresh cards).
    async with Session() as db:
        result = await db.execute(text("""
            SELECT c.id, c.type_line, c.oracle_text
            FROM cards c
            WHERE NOT EXISTS (
                SELECT 1 FROM card_abilities a WHERE a.card_id = c.id
            )
        """))
        rows = result.fetchall()

    log.info("Tagging %d new cards…", len(rows))
    async with Session() as db:
        for row in tqdm(rows):
            card_id = row[0]
            search_text = f"{row[1] or ''}\n{row[2] or ''}"
            inserts = []

            for key, name, pattern in TRIGGER_PATTERNS:
                m = pattern.search(search_text)
                if m:
                    inserts.append({
                        "card_id": str(card_id),
                        "ability_type": "triggered" if "trigger" in name else ("static" if "lord" in name or "anthem" in name else "activated"),
                        "ability_name": name,
                        "trigger_event": key,
                        "effect_class": None,
                        "raw_text": m.group(0),
                    })

            if inserts:
                await db.execute(text("""
                    INSERT INTO card_abilities
                        (card_id, ability_type, ability_name, trigger_event, effect_class, raw_text)
                    VALUES
                        (:card_id, :ability_type, :ability_name, :trigger_event, :effect_class, :raw_text)
                """), inserts)

        await db.commit()

    # ── Pass 1b: gap detection — apply new trigger patterns to existing cards ──
    # When a new trigger_event is added to TRIGGER_PATTERNS after the initial
    # tag_abilities run, existing cards are skipped by Pass 1 (they already have
    # ability rows).  This pass detects trigger_events with 0 consumer rows and
    # re-processes ALL cards for just those new events.
    #
    # We use the pre-Pass-1 snapshot so events that got their first rows from
    # newly ingested cards during Pass 1 are still detected as gaps (they won't
    # have rows on any *existing* cards).
    new_events = [
        (key, name, pattern)
        for key, name, pattern in TRIGGER_PATTERNS
        if key not in existing_events
    ]

    if new_events:
        log.info(
            "%s: %d trigger event(s) — backfilling across all cards: %s",
            "Full rescan" if rescan else "Gap detection",
            len(new_events), [k for k, _, _ in new_events],
        )
        if rescan:
            event_keys = [k for k, _, _ in new_events]
            async with Session() as db:
                await db.execute(
                    text("DELETE FROM card_abilities WHERE trigger_event = ANY(:keys)"),
                    {"keys": event_keys},
                )
                await db.commit()
            log.info("  Rescan: deleted existing rows for %d event(s)", len(event_keys))
        async with Session() as db:
            all_cards_result = await db.execute(text("""
                SELECT c.id, c.type_line, c.oracle_text FROM cards c
            """))
            all_cards = all_cards_result.fetchall()

        log.info("  Scanning %d cards for %d new event(s)…", len(all_cards), len(new_events))
        backfill_count = 0
        async with Session() as db:
            for card_id, type_line, oracle_text in tqdm(all_cards):
                search_text = f"{type_line or ''}\n{oracle_text or ''}"
                inserts = []
                for key, name, pattern in new_events:
                    m = pattern.search(search_text)
                    if m:
                        inserts.append({
                            "card_id": str(card_id),
                            "ability_type": "triggered" if "trigger" in name else ("static" if "lord" in name or "anthem" in name else "activated"),
                            "ability_name": name,
                            "trigger_event": key,
                            "effect_class": None,
                            "raw_text": m.group(0)[:200],
                        })
                if inserts:
                    await db.execute(text("""
                        INSERT INTO card_abilities
                            (card_id, ability_type, ability_name, trigger_event, effect_class, raw_text)
                        VALUES
                            (:card_id, :ability_type, :ability_name, :trigger_event, :effect_class, :raw_text)
                        ON CONFLICT (card_id, ability_type, ability_name, COALESCE(effect_class, '')) DO NOTHING
                    """), inserts)
                    backfill_count += len(inserts)

            await db.commit()
        log.info("  Gap detection complete: %d rows inserted", backfill_count)
    else:
        log.info("Gap detection: all trigger events already have consumer rows — nothing to backfill (use --rescan to force)")

    # ── Pass 2: tribal membership via SQL ─────────────────────────────────────
    # Tribal tags are resolved by type_line / changeling SQL rather than
    # oracle-text regex, so they run as a separate bulk INSERT pass.
    log.info("Tagging tribal membership (%d pattern(s))…", len(TRIBAL_PATTERNS))
    async with Session() as db:
        for key, name, where_sql in TRIBAL_PATTERNS:
            if rescan:
                await db.execute(
                    text("DELETE FROM card_abilities WHERE trigger_event = :key"),
                    {"key": key},
                )
            result = await db.execute(text(f"""
                INSERT INTO card_abilities
                    (card_id, ability_type, ability_name, trigger_event, effect_class, raw_text)
                SELECT id, 'tribal', :name, :key, NULL, NULL
                FROM cards
                WHERE {where_sql}
                ON CONFLICT (card_id, ability_type, ability_name, COALESCE(effect_class, '')) DO NOTHING
            """), {"name": name, "key": key})
            log.info("  %s: %d rows", key, result.rowcount)
        await db.commit()


if __name__ == "__main__":
    import argparse
    import asyncio
    import logging as _logging

    _logging.basicConfig(level=_logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Embed cards and tag abilities")
    parser.add_argument(
        "--stage",
        choices=["embed_cards", "tag_abilities"],
        default=None,
        help="Run a single sub-stage (default: run both)",
    )
    parser.add_argument(
        "--rescan",
        action="store_true",
        help=(
            "tag_abilities only: re-apply every trigger pattern to every card, "
            "not just those with 0 existing rows.  Use after improving a pattern regex."
        ),
    )
    args = parser.parse_args()

    if args.stage == "embed_cards":
        asyncio.run(embed_cards())
    elif args.stage == "tag_abilities":
        asyncio.run(tag_abilities(rescan=args.rescan))
    else:
        async def _run_both():
            await embed_cards()
            await tag_abilities(rescan=args.rescan)
        asyncio.run(_run_both())
