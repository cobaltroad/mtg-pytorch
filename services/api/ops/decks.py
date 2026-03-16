"""Deck generation operations — wires up DeckConstructor model inference."""

from __future__ import annotations

import asyncio
import logging
import os
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "")


def _sync_db_url(db_url: str) -> str:
    return db_url.replace("postgresql+asyncpg://", "postgresql://")


async def generate(
    db: AsyncSession, commander_oracle_id: UUID, checkpoint: str
) -> dict | None:
    """Generate a 99-card deck using the DeckConstructor model.

    Falls back to the synergy-based stub if the model or checkpoint is
    unavailable (logs a warning in that case).
    """
    # Resolve commander card
    result = await db.execute(
        text("""
            SELECT id, oracle_id, name, type_line, oracle_text, color_identity, mana_cost, cmc
            FROM cards WHERE oracle_id = :oid
        """),
        {"oid": str(commander_oracle_id)},
    )
    commander_row = result.fetchone()
    if not commander_row:
        return None

    commander_id = str(commander_row[0])
    color_identity = commander_row[5] or []

    db_url = DATABASE_URL

    # ── Attempt real model inference ─────────────────────────────────────────
    try:
        from ops import inference

        loop = asyncio.get_event_loop()

        # Load model (lazy, cached)
        ckpt_name = checkpoint if checkpoint != "latest" else "phase4_best"
        model = await loop.run_in_executor(None, inference.get_model, ckpt_name)

        if model is not None:
            # Load embeddings (lazy, cached)
            embeddings = await loop.run_in_executor(None, inference.get_embeddings, db_url)

            if embeddings and commander_id in embeddings:
                # Get common context from existing decklists
                context_ids = await loop.run_in_executor(
                    None, inference.get_common_context, commander_id, db_url
                )

                all_ids = list(embeddings.keys())
                # Score all cards
                scored = await loop.run_in_executor(
                    None,
                    inference.score_cards,
                    commander_id, context_ids, embeddings, model, all_ids,
                )

                if scored:
                    top_ids = [cid for cid, _ in scored[:99]]
                    top_scores = {cid: sc for cid, sc in scored[:99]}

                    # Fetch card metadata for top 99
                    card_result = await db.execute(
                        text("""
                            SELECT id::text, oracle_id, name, type_line, oracle_text,
                                   color_identity, mana_cost, cmc
                            FROM cards
                            WHERE id::text = ANY(:ids)
                        """),
                        {"ids": top_ids},
                    )
                    card_rows = card_result.fetchall()
                    card_map = {str(r[0]): r for r in card_rows}

                    # Preserve score ordering
                    cards = []
                    scores = []
                    for cid in top_ids:
                        if cid in card_map:
                            r = card_map[cid]
                            cards.append({
                                "oracle_id": r[1],
                                "name": r[2],
                                "type_line": r[3],
                                "oracle_text": r[4],
                                "color_identity": r[5] or [],
                                "mana_cost": r[6],
                                "cmc": r[7],
                            })
                            scores.append(float(top_scores[cid]))

                    # Resolve context card names for the response
                    context_names: list[str] = []
                    if context_ids:
                        ctx_result = await db.execute(
                            text("""
                                SELECT id::text, name FROM cards
                                WHERE id::text = ANY(:ids)
                            """),
                            {"ids": context_ids},
                        )
                        ctx_name_map = {str(r[0]): r[1] for r in ctx_result.fetchall()}
                        context_names = [
                            ctx_name_map[cid] for cid in context_ids if cid in ctx_name_map
                        ]

                    return {
                        "commander": dict(commander_row._mapping),
                        "cards": cards,
                        "scores": scores,
                        "checkpoint": ckpt_name,
                        "context_cards": context_names,
                    }

    except Exception as exc:
        log.warning("Model inference failed, falling back to synergy stub: %s", exc)

    # ── Synergy-based fallback stub ───────────────────────────────────────────
    log.warning(
        "Using synergy-based stub for commander %s (checkpoint=%s unavailable)",
        commander_oracle_id, checkpoint,
    )
    card_result = await db.execute(
        text("""
            SELECT DISTINCT ON (c.oracle_id)
                c.oracle_id, c.name, c.type_line, c.oracle_text,
                c.color_identity, c.mana_cost, c.cmc,
                coalesce(s.score, 0.0) AS score
            FROM cards c
            LEFT JOIN synergy_edges s
                ON (s.card_a = :cid OR s.card_b = :cid)
                AND (s.card_a = c.id OR s.card_b = c.id)
                AND s.score_type = 'ability_trigger'
            WHERE c.id != :cid
              AND c.color_identity <@ :ci::text[]
              AND c.legalities->>'commander' = 'legal'
            ORDER BY c.oracle_id, score DESC
            LIMIT 99
        """),
        {"cid": commander_id, "ci": color_identity},
    )
    rows = card_result.fetchall()

    cards = [
        {
            "oracle_id": r[0], "name": r[1], "type_line": r[2],
            "oracle_text": r[3], "color_identity": r[4],
            "mana_cost": r[5], "cmc": r[6],
        }
        for r in rows
    ]
    scores = [float(r[7]) for r in rows]

    return {
        "commander": dict(commander_row._mapping),
        "cards": cards,
        "scores": scores,
        "checkpoint": checkpoint,
        "context_cards": [],
    }
