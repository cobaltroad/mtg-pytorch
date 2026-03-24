"""Commander stage — commander-specific synergy edges.

Writes two score_type buckets required by the commander artifact
(export_dataset_commanders):

  commander_value   — low-MV commanders paired with "free-if-commander" and
                      "better-with-commander" support cards.

  ability_trigger   — commander↔tribe-member and member↔member edges for every
  (tribal_*)          tribe in TRIBES (tribal_*_typeline trigger_event).

Entrypoint:  python -m stages.commander
             [--stage compute_commander_value_synergy|compute_tribal_typeline_synergy]

Prerequisites (must be run first):
  stages.download   — cards table populated
  stages.tag        — card_abilities table populated
"""
from __future__ import annotations

import logging
import os

from sqlalchemy import text

from stages.db import Session, SYNERGY_CHUNK, SYNERGY_LIMIT

log = logging.getLogger(__name__)

TRIBAL_MEMBER_LIMIT = int(os.environ.get("TRIBAL_MEMBER_LIMIT", "50_000"))
"""Max intra-tribal member→member edges per tribe (commander→member edges are uncapped)."""

COMMANDER_VALUE_LIMIT = int(os.environ.get("COMMANDER_VALUE_LIMIT", "500_000"))
"""Maximum commander_value edges per trigger_event."""

from synergy import TRIBES, ALL_TYPES_SQL  # noqa: E402
from synergy.commander_value import (  # noqa: E402
    PRODUCER_MAP as COMMANDER_VALUE_PRODUCER_MAP,
    EDGE_SCORES as COMMANDER_VALUE_EDGE_SCORES,
)


async def compute_commander_value_synergy() -> None:
    """Build synergy edges between low-MV commanders and commander-value cards.

    These edges capture the synergy between:

    * **Low-MV commanders** (CMC ≤ 2 legendary creatures / planeswalkers) — the
      *producers* — which are frequently in play and easy to recast, maximising
      the value extracted from commander-conditional support cards.

    * **Commander-value cards** — the *consumers* — whose oracle text grants a
      meaningful benefit specifically when you control your commander:

      - ``commander_free_cast`` (score 1.0): spells that may be cast for free
        while a commander is in play (Deflecting Swat, Fierce Guardianship,
        Flawless Maneuver, Deadly Rollick, …).
      - ``commander_in_play_payoff`` (score 0.8): permanents / spells that gain
        abilities, produce bonus mana, or otherwise improve while a commander is
        present (Loyal Apprentice, Jeska's Will, Loran's Escape, …).
      - ``commander_mana_value`` (score 0.6): cards whose mana output references
        a legendary creature or planeswalker you control (Mox Amber, Selvala
        Heart of the Wilds, …).  For this event the producer pool is widened to
        all legendary creatures/planeswalkers (no CMC cap) because Mox Amber
        works with any legend, not just cheap ones.

    All edges are written with ``score_type = 'commander_value'`` so they are
    kept separate from ``ability_trigger`` edges and can be queried or weighted
    independently during training and deck generation.

    The direction of each edge is:
        card_a = producer (low-MV commander)
        card_b = consumer (commander-value payoff card)

    Color-identity filtering is intentionally skipped here because the
    commander-value cards (e.g. Deflecting Swat) typically belong to a single
    color and would naturally end up in a legal deck — color legality is
    enforced at deck-generation time.
    """
    log.info("Computing commander-value synergy edges…")

    for trigger_event, producer_where in COMMANDER_VALUE_PRODUCER_MAP.items():
        score = COMMANDER_VALUE_EDGE_SCORES.get(trigger_event, 0.6)

        # Fetch producer card IDs (low-MV legendary creatures / planeswalkers)
        async with Session() as db:
            prod_rows = (await db.execute(text(f"""
                SELECT id FROM cards WHERE {producer_where}
            """))).fetchall()
        producer_ids = [str(r[0]) for r in prod_rows]

        if not producer_ids:
            log.info("  commander_value/%s → no producers, skipping", trigger_event)
            continue

        # Fetch consumer card IDs (tagged with this trigger_event in card_abilities)
        async with Session() as db:
            cons_rows = (await db.execute(text(f"""
                SELECT DISTINCT ca.card_id
                FROM card_abilities ca
                WHERE ca.trigger_event = '{trigger_event}'
            """))).fetchall()
        consumer_ids = [str(r[0]) for r in cons_rows]

        if not consumer_ids:
            log.info("  commander_value/%s → no consumers tagged, skipping", trigger_event)
            continue

        total_inserted = 0
        n_chunks = (len(producer_ids) + SYNERGY_CHUNK - 1) // SYNERGY_CHUNK
        log.info(
            "  commander_value/%s: %d producers × %d consumers in %d chunks (score=%.1f)…",
            trigger_event, len(producer_ids), len(consumer_ids), n_chunks, score,
        )

        consumer_list = "'" + "','".join(consumer_ids) + "'"

        for chunk_idx in range(0, len(producer_ids), SYNERGY_CHUNK):
            if total_inserted >= COMMANDER_VALUE_LIMIT:
                log.info(
                    "  commander_value/%s: COMMANDER_VALUE_LIMIT=%d reached, stopping",
                    trigger_event, COMMANDER_VALUE_LIMIT,
                )
                break

            chunk = producer_ids[chunk_idx : chunk_idx + SYNERGY_CHUNK]
            id_list = "'" + "','".join(chunk) + "'"

            async with Session() as db:
                result = await db.execute(text(f"""
                    INSERT INTO synergy_edges
                        (card_a, card_b, score_type, score, metadata)
                    SELECT
                        p.id::uuid,
                        c.id::uuid,
                        'commander_value',
                        {score},
                        '{{"trigger_event": "{trigger_event}"}}'::jsonb
                    FROM (SELECT unnest(ARRAY[{id_list}]::uuid[]) AS id) p
                    CROSS JOIN (SELECT unnest(ARRAY[{consumer_list}]::uuid[]) AS id) c
                    WHERE p.id != c.id
                    ON CONFLICT (card_a, card_b, score_type) DO NOTHING
                """))
                await db.commit()
            total_inserted += result.rowcount

        log.info("  commander_value/%s → %d edges", trigger_event, total_inserted)

    log.info("Commander-value synergy complete")


async def compute_tribal_typeline_synergy() -> None:
    """Build synergy edges between tribal commanders and tribe members.

    Two kinds of edges are generated for each tribe in TRIBES:

    1. Commander → member  (uncapped)
       Legendary creature cards whose oracle text mentions the tribe name
       (e.g. "Zombie", "Elf") paired with every card of that tribe.
       Requiring the tribe to appear in oracle text prevents false positives
       from commanders that merely happen to share a creature type (e.g. a
       Legendary Human with no Human-matters text should not get Human edges).

    2. Member → member  (capped at TRIBAL_MEMBER_LIMIT per tribe)
       All tribe members paired with each other, so intra-tribal co-occurrence
       is reflected in the embedding space.

    Changelings ('Changeling' = ANY(keywords)) are included in every tribe's
    member pool because they are every creature type simultaneously — e.g.
    Mothdust Changeling and Graveshifter count as Zombies for Wilhelt edges.

    Both use score_type='ability_trigger' so Phase 2 training picks them up
    without any changes to train.py.
    """
    log.info("Computing tribal type_line synergy edges…")

    for tribe in TRIBES:
        t = tribe.lower()

        async with Session() as db:
            # Changelings ('Changeling' = ANY(keywords)) are every creature type, so
            # they belong to every tribe's member pool regardless of type_line.
            all_members = (await db.execute(text(f"""
                SELECT id::text FROM cards
                WHERE (
                    (lower(type_line) LIKE '%{t}%' AND lower(type_line) LIKE '%creature%')
                    OR {ALL_TYPES_SQL}
                )
            """))).fetchall()
            # Only commanders whose oracle text explicitly mentions the tribe name
            # qualify for commander→member edges.  Matching solely on type_line
            # would pair every Legendary Human with all Humans, etc., even when
            # the card has no Human-matters text — a major source of false positives.
            commanders = (await db.execute(text(f"""
                SELECT id::text FROM cards
                WHERE lower(type_line) LIKE '%creature%'
                  AND lower(type_line) LIKE '%legendary%'
                  AND lower(oracle_text) LIKE '%{t}%'
            """))).fetchall()

        member_ids = [r[0] for r in all_members]
        cmd_ids    = [r[0] for r in commanders]

        if not member_ids:
            log.info("  %s: no members found, skipping", tribe)
            continue

        log.info("  %s: %d members, %d legendary commanders", tribe, len(member_ids), len(cmd_ids))

        # Check how many edges already exist for this tribe so the "0 new edges"
        # log from ON CONFLICT DO NOTHING isn't misread as a failure.
        async with Session() as db:
            existing_tribal = (await db.execute(text(f"""
                SELECT count(*) FROM synergy_edges
                WHERE metadata->>'trigger_event' = 'tribal_{t}_typeline'
            """))).scalar()
        if existing_tribal:
            log.info("  %s: %d existing typeline edges (new inserts skipped via ON CONFLICT)",
                     tribe, existing_tribal)

        # ── 1. Commander → all tribe members (uncapped) ─────────────────────
        cmd_inserted = 0
        for chunk_start in range(0, len(cmd_ids), SYNERGY_CHUNK):
            chunk = cmd_ids[chunk_start : chunk_start + SYNERGY_CHUNK]
            id_list    = "'" + "','".join(chunk) + "'"
            member_list = "'" + "','".join(member_ids) + "'"
            async with Session() as db:
                result = await db.execute(text(f"""
                    INSERT INTO synergy_edges (card_a, card_b, score_type, score, metadata)
                    SELECT
                        c.id::uuid,
                        m.id::uuid,
                        'ability_trigger',
                        1.0,
                        '{{"trigger_event": "tribal_{t}_typeline", "role": "commander_member"}}'::jsonb
                    FROM (SELECT unnest(ARRAY[{id_list}]::uuid[]) AS id) c
                    CROSS JOIN (SELECT unnest(ARRAY[{member_list}]::uuid[]) AS id) m
                    WHERE c.id != m.id
                    ON CONFLICT (card_a, card_b, score_type) DO NOTHING
                """))
                await db.commit()
            cmd_inserted += result.rowcount
        log.info("    commander→member: %d new edges (existing skipped via ON CONFLICT)", cmd_inserted)

        # ── 2. Member → member (capped) ──────────────────────────────────────
        member_inserted = 0
        for chunk_start in range(0, len(member_ids), SYNERGY_CHUNK):
            if member_inserted >= TRIBAL_MEMBER_LIMIT:
                log.info("    TRIBAL_MEMBER_LIMIT=%d reached for %s, stopping",
                         TRIBAL_MEMBER_LIMIT, tribe)
                break
            chunk = member_ids[chunk_start : chunk_start + SYNERGY_CHUNK]
            id_list     = "'" + "','".join(chunk) + "'"
            member_list = "'" + "','".join(member_ids) + "'"
            async with Session() as db:
                result = await db.execute(text(f"""
                    INSERT INTO synergy_edges (card_a, card_b, score_type, score, metadata)
                    SELECT
                        c.id::uuid,
                        m.id::uuid,
                        'ability_trigger',
                        1.0,
                        '{{"trigger_event": "tribal_{t}_typeline", "role": "member_member"}}'::jsonb
                    FROM (SELECT unnest(ARRAY[{id_list}]::uuid[]) AS id) c
                    CROSS JOIN (SELECT unnest(ARRAY[{member_list}]::uuid[]) AS id) m
                    WHERE c.id != m.id
                    ON CONFLICT (card_a, card_b, score_type) DO NOTHING
                """))
                await db.commit()
            member_inserted += result.rowcount
        log.info("    member→member: %d new edges (existing skipped via ON CONFLICT)", member_inserted)

    log.info("Tribal type_line synergy complete")


if __name__ == "__main__":
    import argparse
    import asyncio
    import logging as _logging

    _logging.basicConfig(level=_logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Compute commander-specific synergy edges")
    parser.add_argument(
        "--stage",
        choices=["compute_commander_value_synergy", "compute_tribal_typeline_synergy"],
        default=None,
        help="Run a single commander synergy stage (default: run both)",
    )
    args = parser.parse_args()

    if args.stage == "compute_commander_value_synergy":
        asyncio.run(compute_commander_value_synergy())
    elif args.stage == "compute_tribal_typeline_synergy":
        asyncio.run(compute_tribal_typeline_synergy())
    else:
        async def _run_both():
            await compute_commander_value_synergy()
            await compute_tribal_typeline_synergy()
        asyncio.run(_run_both())
