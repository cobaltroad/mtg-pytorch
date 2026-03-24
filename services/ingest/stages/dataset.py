"""Dataset stage — compute synergy edges for training datasets.

Writes two distinct score_type buckets:

  ability_trigger         — oracle-text pattern-based edges (PRODUCER_MAP).
                            Consumed by: co-occurrence training path,
                            and by the commander artifact (export_dataset_commanders).

  xmage_ability_trigger   — XMage-class edges (XMAGE_PRODUCER_MAP).
                            Consumed by: compositional training path (export_dataset).

Entrypoint:  python -m stages.dataset [--stage compute_synergy|compute_synergy_xmage]
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from stages.db import Session, SYNERGY_CHUNK, SYNERGY_LIMIT

log = logging.getLogger(__name__)

from synergy import (  # noqa: E402
    PRODUCER_MAP,
    XMAGE_PRODUCER_MAP,
    SPELLCAST_TRIGGER_PRODUCER_MAP,
)


# ── Oracle-text synergy edges ─────────────────────────────────────────────────

async def compute_synergy() -> None:
    """Build synergy edges in small chunked transactions.

    Fetches producer card IDs in Python, then drives INSERT...SELECT statements
    SYNERGY_CHUNK producers at a time so no single transaction materialises more
    than ~200 × consumers rows.  Progress is checkpointed after every chunk so
    a restart resumes without duplicates (ON CONFLICT DO NOTHING).
    """
    log.info("Computing ability-trigger synergy edges…")

    for trigger_event, producer_where in PRODUCER_MAP.items():
        # Fetch just the IDs of producer cards — small result set
        async with Session() as db:
            rows = (await db.execute(text(f"""
                SELECT id FROM cards WHERE {producer_where}
            """))).fetchall()
        producer_ids = [str(r[0]) for r in rows]

        if not producer_ids:
            log.info("  %s → no producers found, skipping", trigger_event)
            continue

        # Count consumers before running chunks — 0 consumers means the
        # trigger_event pattern produced no card_abilities rows (tag_abilities
        # gap) and all chunks would silently produce 0 inserts.
        async with Session() as db:
            consumer_count = (await db.execute(text(f"""
                SELECT COUNT(*) FROM card_abilities
                WHERE trigger_event = '{trigger_event}'
            """))).scalar()
        if not consumer_count:
            log.warning(
                "  %s → 0 consumer rows in card_abilities — skipping "
                "(run tag_abilities to backfill this trigger event)",
                trigger_event,
            )
            continue

        total_inserted = 0
        n_chunks = (len(producer_ids) + SYNERGY_CHUNK - 1) // SYNERGY_CHUNK
        log.info("  %s: %d producers × %d consumers in %d chunks…",
                 trigger_event, len(producer_ids), consumer_count, n_chunks)

        for chunk_idx in range(0, len(producer_ids), SYNERGY_CHUNK):
            if total_inserted >= SYNERGY_LIMIT:
                log.info("  %s: SYNERGY_LIMIT=%d reached, stopping early",
                         trigger_event, SYNERGY_LIMIT)
                break

            chunk = producer_ids[chunk_idx : chunk_idx + SYNERGY_CHUNK]
            id_list = "'" + "','".join(chunk) + "'"

            async with Session() as db:
                result = await db.execute(text(f"""
                    INSERT INTO synergy_edges (card_a, card_b, score_type, score, metadata)
                    SELECT
                        c.id::uuid,
                        ca.card_id,
                        'ability_trigger',
                        1.0,
                        '{{"trigger_event": "{trigger_event}"}}'::jsonb
                    FROM (SELECT unnest(ARRAY[{id_list}]::uuid[]) AS id) c
                    JOIN cards pc ON pc.id = c.id
                    CROSS JOIN (
                        SELECT ca.card_id, cc.color_identity AS consumer_ci
                        FROM card_abilities ca
                        JOIN cards cc ON cc.id = ca.card_id
                        WHERE ca.trigger_event = '{trigger_event}'
                    ) ca
                    WHERE c.id != ca.card_id
                      AND (
                          pc.color_identity = '{{}}'
                          OR ca.consumer_ci = '{{}}'
                          OR pc.color_identity && ca.consumer_ci
                      )
                    ON CONFLICT (card_a, card_b, score_type) DO NOTHING
                """))
                await db.commit()
            total_inserted += result.rowcount

            if (chunk_idx // SYNERGY_CHUNK) % 10 == 0:
                log.info("    chunk %d/%d — %d edges so far",
                         chunk_idx // SYNERGY_CHUNK + 1, n_chunks, total_inserted)

        log.info("  %s → %d new edges (existing skipped via ON CONFLICT)", trigger_event, total_inserted)


# ── XMage-class synergy edges ────────────────────────────────────────────────

async def _xmage_insert_edges(
    ability_class: str,
    producer_where: str,
    trigger_event_filter: str | None = None,
) -> int:
    """Insert xmage_ability_trigger synergy edges for one (ability_class, trigger_event) bucket.

    Returns the number of new rows inserted.

    ``trigger_event_filter`` — when set, only consumers whose ``trigger_event``
    matches this value are included.  Used to sub-group
    ``SpellCastControllerTriggeredAbility`` by spell type.
    """
    async with Session() as db:
        rows = (await db.execute(text(f"""
            SELECT id FROM cards WHERE {producer_where}
        """))).fetchall()
    producer_ids = [str(r[0]) for r in rows]

    if not producer_ids:
        return 0

    # Build the consumer WHERE clause
    consumer_filter = "ca.ability_name = :cls AND ca.source = 'xmage'"
    params: dict = {"cls": ability_class}
    if trigger_event_filter is not None:
        consumer_filter += " AND COALESCE(ca.trigger_event, 'spell_cast') = :te"
        params["te"] = trigger_event_filter

    async with Session() as db:
        consumer_count = (await db.execute(
            text(f"SELECT COUNT(*) FROM card_abilities ca WHERE {consumer_filter}"),
            params,
        )).scalar()

    if not consumer_count:
        return 0

    label = ability_class if trigger_event_filter is None else f"{ability_class}[{trigger_event_filter}]"
    n_chunks = (len(producer_ids) + SYNERGY_CHUNK - 1) // SYNERGY_CHUNK
    log.info("  %s: %d producers × %d consumers in %d chunks…",
             label, len(producer_ids), consumer_count, n_chunks)

    total_inserted = 0
    jsonb_build = (
        f"jsonb_build_object('ability_class', :cls, 'trigger_event', '{trigger_event_filter}')"
        if trigger_event_filter is not None
        else "jsonb_build_object('ability_class', :cls)"
    )

    for chunk_idx in range(0, len(producer_ids), SYNERGY_CHUNK):
        if total_inserted >= SYNERGY_LIMIT:
            log.info("  %s: SYNERGY_LIMIT=%d reached, stopping early", label, SYNERGY_LIMIT)
            break

        chunk = producer_ids[chunk_idx : chunk_idx + SYNERGY_CHUNK]
        id_list = "'" + "','".join(chunk) + "'"

        async with Session() as db:
            result = await db.execute(text(f"""
                INSERT INTO synergy_edges (card_a, card_b, score_type, score, metadata)
                SELECT
                    c.id::uuid,
                    ca.card_id,
                    'xmage_ability_trigger',
                    1.0,
                    {jsonb_build}
                FROM (SELECT unnest(ARRAY[{id_list}]::uuid[]) AS id) c
                JOIN cards pc ON pc.id = c.id
                CROSS JOIN (
                    SELECT ca.card_id, cc.color_identity AS consumer_ci
                    FROM card_abilities ca
                    JOIN cards cc ON cc.id = ca.card_id
                    WHERE {consumer_filter}
                ) ca
                WHERE c.id != ca.card_id
                  AND (
                      pc.color_identity = '{{}}'
                      OR ca.consumer_ci = '{{}}'
                      OR pc.color_identity && ca.consumer_ci
                  )
                ON CONFLICT (card_a, card_b, score_type) DO NOTHING
            """), params)
            await db.commit()
        total_inserted += result.rowcount

        if (chunk_idx // SYNERGY_CHUNK) % 10 == 0:
            log.info("    chunk %d/%d — %d edges so far",
                     chunk_idx // SYNERGY_CHUNK + 1, n_chunks, total_inserted)

    log.info("  %s → %d new edges", label, total_inserted)
    return total_inserted


async def compute_synergy_xmage() -> None:
    """Build XMage-class synergy edges for the compositional training path.

    Reads ``card_abilities`` rows where ``source='xmage'``, groups by
    ``ability_name`` (the raw XMage class name), then cross-joins each consumer
    group with the producer cards defined in ``XMAGE_PRODUCER_MAP``.

    ``SpellCastControllerTriggeredAbility`` is handled specially: consumers are
    sub-grouped by their refined ``trigger_event`` (set by the body-scan in
    ``xmage_parse.py``) and each sub-bucket uses the type-specific producer SQL
    from ``SPELLCAST_TRIGGER_PRODUCER_MAP``.  This prevents Sythis (enchantment
    cast) from being paired with Guttersnipe (instant/sorcery) or Beast Whisperer
    (creature cast) as positive synergy pairs.

    Edges are written with ``score_type='xmage_ability_trigger'`` so they are
    kept entirely separate from the pattern-based ``ability_trigger`` edges used
    by the co-occurrence training path.
    """
    log.info("Computing XMage-class synergy edges (compositional path)…")

    for ability_class, default_producer_where in XMAGE_PRODUCER_MAP.items():
        if ability_class == "SpellCastControllerTriggeredAbility":
            # Sub-group by trigger_event so each spell-type bucket gets the
            # correct producers (enchantments for Sythis, creatures for Beast
            # Whisperer, instants/sorceries for Guttersnipe, etc.)
            async with Session() as db:
                te_rows = (await db.execute(text("""
                    SELECT COALESCE(trigger_event, 'spell_cast') AS te, COUNT(*) AS cnt
                    FROM card_abilities
                    WHERE ability_name = :cls AND source = 'xmage'
                    GROUP BY COALESCE(trigger_event, 'spell_cast')
                """), {"cls": ability_class})).fetchall()

            if not te_rows:
                log.info("  %s → 0 xmage consumers, skipping", ability_class)
                continue

            for trigger_event, cnt in te_rows:
                producer_where = SPELLCAST_TRIGGER_PRODUCER_MAP.get(
                    trigger_event, default_producer_where
                )
                await _xmage_insert_edges(ability_class, producer_where, trigger_event)
            continue

        # All other ability classes: one producer bucket, no trigger_event filter
        inserted = await _xmage_insert_edges(ability_class, default_producer_where)
        if inserted == 0:
            async with Session() as db:
                consumer_count = (await db.execute(text("""
                    SELECT COUNT(*) FROM card_abilities
                    WHERE ability_name = :cls AND source = 'xmage'
                """), {"cls": ability_class})).scalar()
            if not consumer_count:
                log.info("  %s → 0 xmage consumers, skipping", ability_class)
            else:
                log.info("  %s → no producers found, skipping", ability_class)


if __name__ == "__main__":
    import argparse
    import asyncio
    import logging as _logging

    _logging.basicConfig(level=_logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Compute synergy edges for training datasets")
    parser.add_argument(
        "--stage",
        choices=["compute_synergy", "compute_synergy_xmage"],
        default=None,
        help="Run a single synergy stage (default: run both)",
    )
    args = parser.parse_args()

    if args.stage == "compute_synergy":
        asyncio.run(compute_synergy())
    elif args.stage == "compute_synergy_xmage":
        asyncio.run(compute_synergy_xmage())
    else:
        async def _run_both():
            await compute_synergy()
            await compute_synergy_xmage()
        asyncio.run(_run_both())
