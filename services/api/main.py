"""MTG Commander AI — FastAPI service."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, Query, HTTPException, Depends, UploadFile, File, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel

from sqlalchemy import text

from db import get_db, AsyncSession
from ops import cards as card_ops, decks as deck_ops, synergy as synergy_ops, training as training_ops
from ops.commander_analysis import (
    analyze_commander_oracle_text,
    combine_partner_analyses,
    CommanderAnalysis,
    SignalResult,
)

log = logging.getLogger(__name__)

DATABASE_URL      = os.environ.get("DATABASE_URL", "")
ADMIN_TOKEN       = os.environ.get("ADMIN_TOKEN", "")
DATASET_PATH      = Path(os.environ.get("DATASET_PATH",      "/data/mtg_dataset.pt"))
DATASET_META_PATH = Path(os.environ.get("DATASET_META_PATH", "/data/mtg_dataset.json"))

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
    model: str = "sentence-transformers/all-mpnet-base-v2",
    db: AsyncSession = Depends(get_db),
):
    """Vector nearest-neighbour search for semantically similar cards."""
    results = await card_ops.similar(db, oracle_id, limit, model)
    if results is None:
        raise HTTPException(404, "Card or embedding not found")
    return results


# ── Commander analysis ───────────────────────────────────────────────────────

@app.get("/commanders/{oracle_id}/analyze", response_model=CommanderAnalysis)
async def analyze_commander(
    oracle_id: UUID,
    db: AsyncSession = Depends(get_db),
    partner_oracle_id: UUID | None = Query(None, description="Second commander oracle_id for partner pairs"),
):
    """Parse a commander's oracle text and return structured deckbuilding signals.

    Returns detected signals (tribal, combat, counters, MTG rules terms, etc.),
    gaps the parser couldn't interpret, an archetype hint, and a generation
    confidence label.  Pure heuristics — no model inference involved.

    Pass partner_oracle_id for partner-commander pairs; the response will include
    merged signals, a partner_relationship classification (symbiotic / additive /
    color_access), and boost_overrides appropriate for the pair as a unit.
    """
    _CARD_COLS = "name, oracle_text, color_identity, keywords, type_line, cmc"

    result = await db.execute(
        text(f"SELECT {_CARD_COLS} FROM cards WHERE oracle_id = :oid"),
        {"oid": str(oracle_id)},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(404, "Card not found")

    name, oracle_text, color_identity, keywords, type_line, cmc = row
    analysis = analyze_commander_oracle_text(
        oracle_text=oracle_text or "",
        commander_name=name,
        color_identity=list(color_identity or []),
        keywords=list(keywords or []),
        type_line=type_line or "",
        cmc=float(cmc) if cmc is not None else None,
    )

    if partner_oracle_id is None:
        return analysis

    # ── Partner pair: fetch second commander and merge ─────────────────────────
    p_result = await db.execute(
        text(f"SELECT {_CARD_COLS} FROM cards WHERE oracle_id = :oid"),
        {"oid": str(partner_oracle_id)},
    )
    p_row = p_result.fetchone()
    if not p_row:
        raise HTTPException(404, f"Partner card not found: {partner_oracle_id}")

    p_name, p_oracle_text, p_color_identity, p_keywords, p_type_line, p_cmc = p_row
    partner_analysis = analyze_commander_oracle_text(
        oracle_text=p_oracle_text or "",
        commander_name=p_name,
        color_identity=list(p_color_identity or []),
        keywords=list(p_keywords or []),
        type_line=p_type_line or "",
        cmc=float(p_cmc) if p_cmc is not None else None,
    )

    return combine_partner_analyses(
        analysis, partner_analysis,
        oracle_text or "", p_oracle_text or "",
    )


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
    partner_oracle_id: UUID | None = None  # second commander for partner pairs
    checkpoint: str = "latest"
    boost_overrides: list[str] = []
    combo_boost: float = 0.3


class ComboPackageOut(BaseModel):
    spellbook_id: str
    produces: list[str]
    cards_included: list[str]
    cards_missing: list[str]
    completion: float
    package_weight: float
    boost_applied: bool = False


class DeckOut(BaseModel):
    commander: CardOut
    cards: list[CardOut]
    scores: list[float]
    checkpoint: str
    context_cards: list[str] = []
    proxy_context: bool = False
    combo_packages_triggered: list[ComboPackageOut] = []


@app.post("/decks/generate", response_model=DeckOut)
async def generate_deck(req: DeckRequest, db: AsyncSession = Depends(get_db)):
    """Ask the model to build a 99-card commander deck."""
    result = await deck_ops.generate(
        db, req.commander_oracle_id, req.checkpoint,
        boost_overrides=req.boost_overrides or None,
        combo_boost=req.combo_boost,
        partner_oracle_id=req.partner_oracle_id,
    )
    if result is None:
        raise HTTPException(400, "Could not generate deck — commander not found or model unavailable")
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


# ── Checkpoint upload ─────────────────────────────────────────────────────────

@app.post("/admin/checkpoint")
async def upload_checkpoint(
    file: UploadFile = File(...),
    x_admin_token: str = Header(default=""),
    name: str = "phase4_best",
):
    """Upload a .pt checkpoint file and hot-swap it into the model cache."""
    if ADMIN_TOKEN and x_admin_token != ADMIN_TOKEN:
        raise HTTPException(403, "Invalid admin token")
    if not file.filename or not file.filename.endswith(".pt"):
        raise HTTPException(400, "File must be a .pt checkpoint")

    from ops import inference
    dest = inference.CHECKPOINT_DIR / f"{name}.pt"
    tmp  = dest.with_suffix(".pt.tmp")

    try:
        data = await file.read()
        tmp.write_bytes(data)
        tmp.replace(dest)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise HTTPException(500, f"Write failed: {exc}")

    # Evict cached model so next inference loads the new weights
    inference._model_cache.pop(name, None)
    log.info("Checkpoint uploaded and cache cleared: %s (%d bytes)", dest, len(data))
    return {"saved": str(dest), "bytes": len(data), "cache_cleared": True}


# ── Training dataset ──────────────────────────────────────────────────────────

@app.get("/dataset/info")
async def dataset_info():
    """Return metadata about the current training artifact (no auth required)."""
    if not DATASET_META_PATH.exists():
        raise HTTPException(
            404,
            "No training dataset available — run the export_dataset pipeline stage first",
        )
    meta = json.loads(DATASET_META_PATH.read_text())
    size_bytes = DATASET_PATH.stat().st_size if DATASET_PATH.exists() else 0
    return {**meta, "size_bytes": size_bytes}


@app.get("/dataset/download")
async def dataset_download():
    """Stream the training artifact (.pt) to the caller (no auth required).

    The artifact is ~100–300 MB and contains embeddings, synergy pairs,
    decks, and pre-computed Phase 4 positions.  The GPU trainer loads it
    with --dataset <path> to train all phases without a database connection.
    """
    if not DATASET_PATH.exists():
        raise HTTPException(
            404,
            "No training dataset available — run the export_dataset pipeline stage first",
        )
    return FileResponse(
        DATASET_PATH,
        media_type="application/octet-stream",
        filename="mtg_dataset.pt",
    )
