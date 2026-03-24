"""Tag stage — embed cards and tag their abilities.

Entrypoint:  python -m stages.tag [--rescan]

Sub-stages:
  embed_cards   — compute sentence-transformer embeddings → card_embeddings
  tag_abilities — oracle-text pattern matching → card_abilities
                  Pass 1:  keyword / triggered abilities
                  Pass 1b: gap detection (backfill new trigger_events)
                  Pass 2:  functional role tags
"""
from __future__ import annotations

import logging
import re

from sqlalchemy import text
from tqdm import tqdm

from stages.db import BATCH_SIZE, EMBEDDING_MODEL, Session

log = logging.getLogger(__name__)

# ── Stage 3: Embed ────────────────────────────────────────────────────────────

from land_tags import annotate_land_oracle  # noqa: E402

from synergy import (  # noqa: E402
    ROLE_PATTERNS,
    LAND_ROLE_PATTERNS,
    TRIGGER_PATTERNS,
    is_land_card,
)

KEYWORD_RE = re.compile(
    r"\b(flying|trample|haste|vigilance|deathtouch|lifelink|reach|hexproof|"
    r"indestructible|flash|first strike|double strike|menace|prowess|"
    r"ward|protection|shroud|defender|annihilator|cascade|convoke|"
    r"delve|exploit|fabricate|flashback|kicker|madness|miracle|"
    r"morph|overload|persist|proliferate|rebound|replicate|retrace|"
    r"scry|storm|suspend|threshold|undying|unearth|wither)\b",
    re.IGNORECASE,
)


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

def _tag_roles(oracle_text: str, type_line: str) -> list[dict]:
    """Return a list of role-tag dicts for a single card.

    Applies :data:`ROLE_PATTERNS` against *oracle_text* and, when the card is
    a Land, also applies :data:`LAND_ROLE_PATTERNS`.  Each matching role is
    emitted at most once per card (duplicates within a single card are dropped).

    Args:
        oracle_text: The card's oracle text (may contain newlines for MDFCs).
        type_line:   The card's type line (e.g. "Legendary Creature — Zombie").

    Returns:
        A list of ``card_abilities``-shaped dicts with ``ability_type='role'``.
    """
    # Deduplication key is (role_name, effect_class) so a card can receive
    # multiple rows for the same flat role if it has distinct effect subtypes
    # (e.g. a card that both destroys creatures and exiles enchantments).
    seen_role_classes: set[tuple[str, str]] = set()
    rows: list[dict] = []

    patterns = list(ROLE_PATTERNS)
    if is_land_card(type_line):
        patterns = patterns + list(LAND_ROLE_PATTERNS)

    for pattern, role_name, effect_class in patterns:
        if (role_name, effect_class) in seen_role_classes:
            continue
        m = re.search(pattern, oracle_text, re.IGNORECASE)
        if m:
            seen_role_classes.add((role_name, effect_class))
            rows.append({
                "ability_type": "role",
                "ability_name": role_name,
                "trigger_event": None,
                "effect_class": effect_class,
                "raw_text": m.group(0)[:200],
            })

    return rows


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

    # ── Pass 1: keyword / triggered / activated abilities ─────────────────────
    # Processes cards that have no ability rows yet (fresh cards).
    async with Session() as db:
        result = await db.execute(text("""
            SELECT c.id, c.oracle_text, c.keywords
            FROM cards c
            WHERE NOT EXISTS (
                SELECT 1 FROM card_abilities a WHERE a.card_id = c.id
            )
            AND c.oracle_text IS NOT NULL
        """))
        rows = result.fetchall()

    log.info("Tagging %d cards (keyword/triggered)…", len(rows))
    async with Session() as db:
        for row in tqdm(rows):
            card_id, oracle_text, kw_list = row[0], row[1] or "", row[2] or []
            inserts = []

            for kw in kw_list:
                inserts.append({
                    "card_id": str(card_id),
                    "ability_type": "keyword",
                    "ability_name": kw,
                    "trigger_event": None,
                    "effect_class": None,
                    "raw_text": kw,
                })

            for pattern, name, event in TRIGGER_PATTERNS:
                m = re.search(pattern, oracle_text, re.IGNORECASE)
                if m:
                    inserts.append({
                        "card_id": str(card_id),
                        "ability_type": "triggered" if "trigger" in name else ("static" if "lord" in name or "anthem" in name else "activated"),
                        "ability_name": name,
                        "trigger_event": event,
                        "effect_class": None,
                        "raw_text": m.group(0),
                    })

            for m in KEYWORD_RE.finditer(oracle_text):
                kw = m.group(0).lower()
                if kw not in [k.lower() for k in kw_list]:
                    inserts.append({
                        "card_id": str(card_id),
                        "ability_type": "keyword",
                        "ability_name": kw.title(),
                        "trigger_event": None,
                        "effect_class": None,
                        "raw_text": kw,
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
        (pattern, name, event)
        for pattern, name, event in TRIGGER_PATTERNS
        if event and event not in existing_events
    ]

    if new_events:
        log.info(
            "%s: %d trigger event(s) — backfilling across all cards: %s",
            "Full rescan" if rescan else "Gap detection",
            len(new_events), [e for _, _, e in new_events],
        )
        async with Session() as db:
            all_cards_result = await db.execute(text("""
                SELECT c.id, c.oracle_text FROM cards c WHERE c.oracle_text IS NOT NULL
            """))
            all_cards = all_cards_result.fetchall()

        log.info("  Scanning %d cards for %d new event(s)…", len(all_cards), len(new_events))
        backfill_count = 0
        async with Session() as db:
            for card_id, oracle_text in tqdm(all_cards):
                inserts = []
                for pattern, name, event in new_events:
                    m = re.search(pattern, oracle_text, re.IGNORECASE)
                    if m:
                        inserts.append({
                            "card_id": str(card_id),
                            "ability_type": "triggered" if "trigger" in name else ("static" if "lord" in name or "anthem" in name else "activated"),
                            "ability_name": name,
                            "trigger_event": event,
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

    # ── Pass 2: functional role tags ──────────────────────────────────────────
    # Incremental: processes cards that have no 'role' ability rows yet.
    # Rescan: deletes all ingest-written role rows (effect_class IS NULL —
    # the old pre-structured-effect_class format) then re-processes every card,
    # writing the new structured effect_class values.  API-written role rows
    # (non-null effect_class in the old API naming convention) are left in place;
    # they coexist harmlessly in card_abilities and are gradually superseded as
    # backfill_roles re-runs against the updated tag_card_roles() in the API.
    if rescan:
        async with Session() as db:
            deleted = await db.execute(text("""
                DELETE FROM card_abilities
                WHERE ability_type = 'role' AND effect_class IS NULL
            """))
            await db.commit()
        log.info("Rescan: deleted %d stale role rows (effect_class IS NULL)", deleted.rowcount)

    async with Session() as db:
        if rescan:
            result = await db.execute(text("""
                SELECT c.id, c.oracle_text, c.type_line
                FROM cards c
                WHERE c.oracle_text IS NOT NULL
            """))
        else:
            result = await db.execute(text("""
                SELECT c.id, c.oracle_text, c.type_line
                FROM cards c
                WHERE NOT EXISTS (
                    SELECT 1 FROM card_abilities a
                    WHERE a.card_id = c.id AND a.ability_type = 'role'
                )
                AND c.oracle_text IS NOT NULL
            """))
        role_rows = result.fetchall()

    log.info("Tagging roles for %d cards…", len(role_rows))
    async with Session() as db:
        for row in tqdm(role_rows):
            card_id, oracle_text, type_line = row[0], row[1] or "", row[2] or ""
            role_inserts = [
                {**rd, "card_id": str(card_id)}
                for rd in _tag_roles(oracle_text, type_line)
            ]
            if role_inserts:
                await db.execute(text("""
                    INSERT INTO card_abilities
                        (card_id, ability_type, ability_name, trigger_event, effect_class, raw_text)
                    VALUES
                        (:card_id, :ability_type, :ability_name, :trigger_event, :effect_class, :raw_text)
                    ON CONFLICT (card_id, ability_type, ability_name, COALESCE(effect_class, '')) DO NOTHING
                """), role_inserts)

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
