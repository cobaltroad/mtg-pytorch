"""MTG Commander AI — FastAPI service."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, Query, HTTPException, Depends
from pydantic import BaseModel

from db import get_db, AsyncSession
from ops import cards as card_ops, decks as deck_ops, synergy as synergy_ops, training as training_ops
from ops import deck_browser as browser_ops

log = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "")

app = FastAPI(
    title="MTG Commander AI",
    version="0.1.0",
    description="Card similarity search, synergy queries, and commander deck generation.",
)


# ── Startup: pre-load embeddings in background ────────────────────────────────

@app.on_event("startup")
async def _preload_embeddings():
    """Fire-and-forget embedding pre-load so the first generate request isn't slow."""
    if not DATABASE_URL:
        return

    async def _load():
        try:
            from ops import inference
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, inference.get_embeddings, DATABASE_URL)
            log.info("Embeddings pre-loaded successfully")
        except Exception as exc:
            log.warning("Background embedding pre-load failed: %s", exc)

    asyncio.create_task(_load())


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
    context_cards: list[str] = []


@app.post("/decks/generate", response_model=DeckOut)
async def generate_deck(req: DeckRequest, db: AsyncSession = Depends(get_db)):
    """Ask the model to build a 99-card commander deck."""
    result = await deck_ops.generate(db, req.commander_oracle_id, req.checkpoint)
    if result is None:
        raise HTTPException(400, "Could not generate deck — commander not found or model unavailable")
    return result


# ── Deck import ──────────────────────────────────────────────────────────────

class DeckImportRequest(BaseModel):
    text: str
    deck_name: str = "Untitled"


class DeckImportResult(BaseModel):
    ok: bool
    commander: str | None
    cards_imported: int
    unresolved: list[str]
    duplicate: bool
    message: str
    deck_id: str | None = None
    analysis: dict | None = None


@app.post("/decks/import", response_model=DeckImportResult)
async def import_deck(req: DeckImportRequest, db: AsyncSession = Depends(get_db)):
    """Parse and import a pasted Moxfield-format decklist."""
    from ops.import_utils import import_decklist_text
    return await import_decklist_text(req.text, req.deck_name, db)


# ── Deck browser ─────────────────────────────────────────────────────────────

@app.get("/decks")
async def list_decks(db: AsyncSession = Depends(get_db)):
    """Return all human-imported decks with commander info and card count."""
    return await browser_ops.list_decks(db)


@app.get("/decks/{deck_id}/browse")
async def browse_deck(deck_id: str, db: AsyncSession = Depends(get_db)):
    """Return a single deck with all cards annotated by role tags.

    Side effects: writes role tags to card_abilities and role_demand synergy
    edges from the commander to role-matching cards (the feedback loop).
    """
    result = await browser_ops.get_deck_with_roles(db, deck_id)
    if result is None:
        raise HTTPException(404, "Deck not found")
    return result


class VoteEntry(BaseModel):
    # Either (card_id + role) for a card-level vote, or (archetype) for deck-level
    card_id:   str | None = None
    role:      str | None = None
    archetype: str | None = None
    vote:      int        # +1 promote, -1 dissuade


class VoteRequest(BaseModel):
    votes: list[VoteEntry]


@app.post("/decks/{deck_id}/vote")
async def vote_deck_analysis(deck_id: str, req: VoteRequest, db: AsyncSession = Depends(get_db)):
    """Store user votes on the model's first-pass role / archetype analysis.

    Votes are persisted in decks.metadata['votes'] and used by /amend to
    adjust role_demand edge scores (promoted → 2× score, dissuaded → removed).
    """
    return await browser_ops.apply_votes(db, deck_id, req.votes)


@app.post("/decks/{deck_id}/amend")
async def amend_deck_analysis(deck_id: str, db: AsyncSession = Depends(get_db)):
    """Re-run analysis incorporating stored votes and update training edges."""
    result = await browser_ops.amend_with_votes(db, deck_id)
    if result is None:
        raise HTTPException(404, "Deck not found")
    return result


# ── Deck metrics ─────────────────────────────────────────────────────────────

@app.get("/decks/metrics")
async def deck_metrics():
    """Return cached Recall@K metrics for the current phase4_best checkpoint."""
    if not DATABASE_URL:
        raise HTTPException(503, "DATABASE_URL not configured")

    try:
        from ops import inference
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: inference.recall_at_k(DATABASE_URL, checkpoint_name="phase4_best"),
        )
        return result
    except Exception as exc:
        log.error("Failed to compute deck metrics: %s", exc)
        raise HTTPException(500, f"Metrics computation failed: {exc}")


# ── Training ──────────────────────────────────────────────────────────────────

class TrainRequest(BaseModel):
    phase: int
    epochs: int = 50
    lr: float = 1e-4
    resume: bool = True
    freeze_encoder: bool = True
    encoder_lr_scale: float = 0.1
    temp_start: float = 0.5
    temp_end: float = 0.05
    sample: int = 500_000
    role_demand_sample: int = 100_000


@app.post("/train/start")
async def start_training(req: TrainRequest):
    """Launch a trainer container for the requested phase."""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: training_ops.start_training(
            phase=req.phase,
            epochs=req.epochs,
            lr=req.lr,
            resume=req.resume,
            freeze_encoder=req.freeze_encoder,
            encoder_lr_scale=req.encoder_lr_scale,
            temp_start=req.temp_start,
            temp_end=req.temp_end,
            sample=req.sample,
            role_demand_sample=req.role_demand_sample,
        ),
    )
    if "error" in result:
        raise HTTPException(500, result["error"])
    return result


@app.get("/train/runs")
async def list_runs():
    """List recent trainer containers."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, training_ops.list_training_runs)


@app.get("/train/logs/{container_id}")
async def get_logs(container_id: str, tail: int = 100):
    """Fetch recent log lines from a trainer container."""
    loop = asyncio.get_event_loop()
    logs = await loop.run_in_executor(
        None, lambda: training_ops.get_logs(container_id, tail)
    )
    return {"logs": logs}


@app.post("/train/stop/{container_id}")
async def stop_training(container_id: str):
    """Stop a running trainer container."""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: training_ops.stop_training(container_id)
    )
    if not result.get("ok"):
        raise HTTPException(500, result.get("error", "Unknown error"))
    return result
