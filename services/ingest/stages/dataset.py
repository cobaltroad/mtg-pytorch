"""Dataset stage — compute synergy edges for training datasets.

Writes three distinct score_type buckets:

  ability_trigger         — oracle-text pattern-based edges (PRODUCER_MAP).
                            Consumed by: co-occurrence training path,
                            and by the commander artifact (export_dataset_commanders).

  xmage_ability_trigger   — XMage-class edges (XMAGE_PRODUCER_MAP).
                            Consumed by: compositional training path (export_dataset).

  effect_peer             — peer edges between cards sharing the same
                            (trigger_event, effect_class) in card_abilities
                            (source='xmage').  No producer concept — purely a
                            functional-role similarity signal.  Fixes the Phase 2
                            creature-cast super-cluster: Beast Whisperer, Guardian
                            Project, and Lifecrafter's Bestiary share
                            (creature_cast, draw) edges; Impact Tremors, Purphoros,
                            and Warstorm Surge share (creature_cast, damage) edges.
                            Consumed by: compositional training path (export_dataset).
                            Requires tag_abilities_xmage to have been run first.

Entrypoint:  python -m stages.dataset
             [--stage compute_textmatch_synergy|compute_xmage_synergy|compute_xmage_effect_synergy]
"""
from __future__ import annotations

import logging

from sqlalchemy import text

import os

from stages.db import Session, SYNERGY_CHUNK, SYNERGY_LIMIT

log = logging.getLogger(__name__)

EFFECT_PEER_LIMIT = int(os.environ.get("EFFECT_PEER_LIMIT", "500_000"))
"""Maximum effect_peer edges total (across all (trigger_event, effect_class) groups)."""

from synergy import PRODUCER_MAP  # noqa: E402

def _xmage_maps():
    """Lazy import — only available once xmage sub-module is wired into synergy/__init__.py."""
    from synergy import XMAGE_PRODUCER_MAP, SPELLCAST_TRIGGER_PRODUCER_MAP
    return XMAGE_PRODUCER_MAP, SPELLCAST_TRIGGER_PRODUCER_MAP


# ── Oracle-text synergy edges ─────────────────────────────────────────────────

async def _reset_score_type(score_type: str) -> None:
    """Delete all edges of one score_type before a rebuild.

    ON CONFLICT DO NOTHING preserves stale rows forever otherwise — the DB
    carried ability_trigger edges from before the color-identity filter
    existed in this file (issue #138), the same drift class the decompose
    stage had (#137).  A stage re-run now yields exactly what current code
    and tags produce.
    """
    async with Session() as db:
        result = await db.execute(
            text("DELETE FROM synergy_edges WHERE score_type = :st"),
            {"st": score_type},
        )
        await db.commit()
    log.info("Deleted %d stale %s edges before rebuild", result.rowcount, score_type)


async def compute_textmatch_synergy() -> None:
    """Rebuild ability_trigger synergy edges in small chunked transactions.

    Existing ability_trigger edges are deleted first (see _reset_score_type).
    Fetches producer card IDs in Python, then drives INSERT...SELECT statements
    SYNERGY_CHUNK producers at a time so no single transaction materialises more
    than ~200 × consumers rows.  Progress within a run is checkpointed after
    every chunk (ON CONFLICT DO NOTHING); an interrupted run restarts from the
    delete.
    """
    log.info("Computing ability-trigger synergy edges…")
    await _reset_score_type("ability_trigger")

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


async def compute_xmage_synergy() -> None:
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
    await _reset_score_type("xmage_ability_trigger")
    XMAGE_PRODUCER_MAP, SPELLCAST_TRIGGER_PRODUCER_MAP = _xmage_maps()

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


async def compute_xmage_effect_synergy() -> None:
    """Build peer synergy edges between cards sharing the same (trigger_event, effect_class).

    Reads ``card_abilities`` rows where ``source='xmage'`` and groups cards by
    ``(trigger_event, effect_class)``.  Every card in a group gets a direct
    ``effect_peer`` edge to every other card in that group.

    This is a pure functional-role similarity signal — no producer/consumer
    concept.  Examples:

    * ``(creature_cast, draw)`` → Beast Whisperer ↔ Guardian Project ↔
      Lifecrafter's Bestiary ↔ The Great Henge ↔ …
    * ``(creature_cast, damage)`` → Impact Tremors ↔ Purphoros ↔
      Warstorm Surge ↔ Terror of the Peaks ↔ …

    Without these edges Phase 2 training collapses all creature-cast trigger
    consumers into one super-cluster because they share the same ~6,000 creature
    producers.  These peer edges directly counteract that collapse.

    Requires ``tag_abilities_xmage`` to have been run first.  Groups with only
    one card are skipped (no peer to pair with).  Processing stops once
    EFFECT_PEER_LIMIT total edges have been inserted across all groups.

    Edges are written with ``score_type='effect_peer'`` so they are kept
    separate from producer→consumer edges and can be sampled independently
    during export.
    """
    log.info("Computing effect-peer synergy edges…")
    await _reset_score_type("effect_peer")

    # Fetch all (trigger_event, effect_class) groups that have >1 distinct card.
    async with Session() as db:
        group_rows = (await db.execute(text("""
            SELECT trigger_event, effect_class,
                   array_agg(DISTINCT card_id::text ORDER BY card_id::text) AS card_ids
            FROM card_abilities
            WHERE source        = 'xmage'
              AND trigger_event IS NOT NULL
              AND effect_class  IS NOT NULL
            GROUP BY trigger_event, effect_class
            HAVING count(DISTINCT card_id) > 1
            ORDER BY trigger_event, effect_class
        """))).fetchall()

    if not group_rows:
        log.warning(
            "No (trigger_event, effect_class) groups found in card_abilities "
            "(source='xmage') — run tag_abilities_xmage first."
        )
        return

    total_inserted = 0
    for trigger_event, effect_class, card_ids in group_rows:
        if total_inserted >= EFFECT_PEER_LIMIT:
            log.info("EFFECT_PEER_LIMIT=%d reached, stopping", EFFECT_PEER_LIMIT)
            break

        n = len(card_ids)
        peer_list = "'" + "','".join(card_ids) + "'"
        # Escape for jsonb literal — values from DB should be alphanumeric/underscore
        meta = (
            f'{{"trigger_event": "{trigger_event}", "effect_class": "{effect_class}"}}'
        )

        group_inserted = 0
        n_chunks = (n + SYNERGY_CHUNK - 1) // SYNERGY_CHUNK
        log.info(
            "  effect_peer/%s/%s: %d cards, %d potential edges, %d chunks…",
            trigger_event, effect_class, n, n * (n - 1), n_chunks,
        )

        for chunk_start in range(0, n, SYNERGY_CHUNK):
            chunk = card_ids[chunk_start : chunk_start + SYNERGY_CHUNK]
            id_list = "'" + "','".join(chunk) + "'"

            async with Session() as db:
                result = await db.execute(text(f"""
                    INSERT INTO synergy_edges (card_a, card_b, score_type, score, metadata)
                    SELECT
                        a.id::uuid,
                        b.id::uuid,
                        'effect_peer',
                        1.0,
                        '{meta}'::jsonb
                    FROM (SELECT unnest(ARRAY[{id_list}]::uuid[]) AS id) a
                    CROSS JOIN (SELECT unnest(ARRAY[{peer_list}]::uuid[]) AS id) b
                    WHERE a.id != b.id
                    ON CONFLICT (card_a, card_b, score_type) DO NOTHING
                """))
                await db.commit()
            group_inserted += result.rowcount

        total_inserted += group_inserted
        log.info("    → %d new edges", group_inserted)

    log.info("Effect-peer synergy complete: %d total edges", total_inserted)


if __name__ == "__main__":
    import argparse
    import asyncio
    import logging as _logging

    _logging.basicConfig(level=_logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Compute synergy edges for training datasets")
    parser.add_argument(
        "--stage",
        choices=["compute_textmatch_synergy", "compute_xmage_synergy", "compute_xmage_effect_synergy"],
        default=None,
        help="Run a single synergy stage (default: run all three)",
    )
    args = parser.parse_args()

    if args.stage == "compute_textmatch_synergy":
        asyncio.run(compute_textmatch_synergy())
    elif args.stage == "compute_xmage_synergy":
        asyncio.run(compute_xmage_synergy())
    elif args.stage == "compute_xmage_effect_synergy":
        asyncio.run(compute_xmage_effect_synergy())
    else:
        async def _run_all():
            await compute_textmatch_synergy()
            await compute_xmage_synergy()
            await compute_xmage_effect_synergy()
        asyncio.run(_run_all())
