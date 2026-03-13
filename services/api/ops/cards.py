"""Card query operations."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def search(db: AsyncSession, q: str, limit: int) -> list[dict]:
    result = await db.execute(
        text("""
            SELECT oracle_id, name, type_line, oracle_text, color_identity, mana_cost, cmc
            FROM cards
            WHERE to_tsvector('english', coalesce(name,'') || ' ' || coalesce(oracle_text,''))
                  @@ plainto_tsquery('english', :q)
            ORDER BY ts_rank(
                to_tsvector('english', coalesce(name,'') || ' ' || coalesce(oracle_text,'')),
                plainto_tsquery('english', :q)
            ) DESC
            LIMIT :limit
        """),
        {"q": q, "limit": limit},
    )
    return [dict(r._mapping) for r in result]


async def get_by_oracle_id(db: AsyncSession, oracle_id: UUID) -> dict | None:
    result = await db.execute(
        text("""
            SELECT oracle_id, name, type_line, oracle_text, color_identity, mana_cost, cmc
            FROM cards WHERE oracle_id = :oid
        """),
        {"oid": str(oracle_id)},
    )
    row = result.fetchone()
    return dict(row._mapping) if row else None


async def similar(
    db: AsyncSession, oracle_id: UUID, limit: int, model: str
) -> list[dict] | None:
    # Fetch the source embedding
    emb_result = await db.execute(
        text("""
            SELECT e.embedding
            FROM card_embeddings e
            JOIN cards c ON c.id = e.card_id
            WHERE c.oracle_id = :oid AND e.model = :model
        """),
        {"oid": str(oracle_id), "model": model},
    )
    row = emb_result.fetchone()
    if not row:
        return None

    result = await db.execute(
        text("""
            SELECT c.oracle_id, c.name, c.type_line, c.oracle_text,
                   c.color_identity, c.mana_cost, c.cmc
            FROM card_embeddings e
            JOIN cards c ON c.id = e.card_id
            WHERE e.model = :model
              AND c.oracle_id != :oid
            ORDER BY e.embedding <=> :emb
            LIMIT :limit
        """),
        {"model": model, "oid": str(oracle_id), "emb": row[0], "limit": limit},
    )
    return [dict(r._mapping) for r in result]
