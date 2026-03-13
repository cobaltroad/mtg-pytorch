"""Synergy query operations."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def top_partners(
    db: AsyncSession, oracle_id: UUID, score_type: str, limit: int
) -> list[dict]:
    result = await db.execute(
        text("""
            SELECT
                s.card_a,
                s.card_b,
                s.score,
                s.score_type
            FROM synergy_edges s
            JOIN cards c ON c.id = s.card_a OR c.id = s.card_b
            WHERE c.oracle_id = :oid
              AND s.score_type = :score_type
            ORDER BY s.score DESC
            LIMIT :limit
        """),
        {"oid": str(oracle_id), "score_type": score_type, "limit": limit},
    )
    return [dict(r._mapping) for r in result]
