"""MTG Commander AI — FastAPI service."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, Query, HTTPException, Depends
from pydantic import BaseModel

from db import get_db, AsyncSession
from ops import cards as card_ops, decks as deck_ops, synergy as synergy_ops

app = FastAPI(
    title="MTG Commander AI",
    version="0.1.0",
    description="Card similarity search, synergy queries, and commander deck generation.",
)


# ── Health ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


# ── Cards ────────────────────────────────────────────────────────────────────

class CardOut(BaseModel):
    oracle_id: UUID
    name: str
    type_line: str | None
    oracle_text: str | None
    color_identity: list[str]
    mana_cost: str | None
    cmc: float | None

    model_config = {"from_attributes": True}


@app.get("/cards/search", response_model=list[CardOut])
async def search_cards(
    q: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    db: AsyncSession = Depends(get_db),
):
    """Full-text search across card name and oracle text."""
    return await card_ops.search(db, q, limit)


@app.get("/cards/{oracle_id}", response_model=CardOut)
async def get_card(oracle_id: UUID, db: AsyncSession = Depends(get_db)):
    card = await card_ops.get_by_oracle_id(db, oracle_id)
    if not card:
        raise HTTPException(404, "Card not found")
    return card


@app.get("/cards/{oracle_id}/similar", response_model=list[CardOut])
async def similar_cards(
    oracle_id: UUID,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    model: str = "sentence-transformers/all-MiniLM-L6-v2",
    db: AsyncSession = Depends(get_db),
):
    """Vector nearest-neighbour search for semantically similar cards."""
    results = await card_ops.similar(db, oracle_id, limit, model)
    if results is None:
        raise HTTPException(404, "Card or embedding not found")
    return results


# ── Synergy ──────────────────────────────────────────────────────────────────

class SynergyPair(BaseModel):
    card_a: UUID
    card_b: UUID
    score: float
    score_type: str


@app.get("/synergy", response_model=list[SynergyPair])
async def get_synergies(
    oracle_id: UUID,
    score_type: str = "ability_trigger",
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    db: AsyncSession = Depends(get_db),
):
    """Return top synergy partners for a card."""
    return await synergy_ops.top_partners(db, oracle_id, score_type, limit)


# ── Deck generation ──────────────────────────────────────────────────────────

class DeckRequest(BaseModel):
    commander_oracle_id: UUID
    checkpoint: str = "latest"


class DeckOut(BaseModel):
    commander: CardOut
    cards: list[CardOut]
    scores: list[float]
    checkpoint: str


@app.post("/decks/generate", response_model=DeckOut)
async def generate_deck(req: DeckRequest, db: AsyncSession = Depends(get_db)):
    """Ask the model to build a 99-card commander deck."""
    result = await deck_ops.generate(db, req.commander_oracle_id, req.checkpoint)
    if result is None:
        raise HTTPException(400, "Could not generate deck — commander not found or model unavailable")
    return result
