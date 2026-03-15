"""Deck generation operations — thin wrapper that calls the trainer checkpoint."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def generate(
    db: AsyncSession, commander_oracle_id: UUID, checkpoint: str
) -> dict | None:
    """Load the requested model checkpoint and generate a 99-card deck.

    This is a placeholder that returns a synergy-ranked stub until the
    training pipeline produces real checkpoints.  Swap the body of
    _run_model() once trainer/model.py is functional.
    """
    # Resolve commander
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

    commander_id = commander_row[0]
    color_identity = commander_row[5] or []  # color_identity column index

    # Stub: return top synergy partners within colour identity, ordered by score
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
        {"cid": str(commander_id), "ci": color_identity},
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
    }
