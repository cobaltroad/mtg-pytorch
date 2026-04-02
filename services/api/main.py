"""MTG Commander AI — FastAPI service."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import FastAPI, Query, HTTPException, Depends, UploadFile, File, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel

from sqlalchemy import text

from db import get_db, SessionLocal, AsyncSession
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
DATASET_CMD_PATH       = Path(os.environ.get("DATASET_CMD_PATH",       "/data/mtg_commanders.pt"))
DATASET_CMD_META_PATH  = Path(os.environ.get("DATASET_CMD_META_PATH",  "/data/mtg_commanders.json"))
DECK_SAVE_DIR     = Path(os.environ.get("DECK_SAVE_DIR",     "/app/generated_decks"))

app = FastAPI(
    title="MTG Commander AI",
    version="0.1.0",
    description="Card similarity search, synergy queries, and commander deck generation.",
)

# ── In-memory job store ───────────────────────────────────────────────────────
# { job_id: {"status": str, "progress": float, "message": str,
#            "result": dict|None, "error": str|None, "created_at": datetime} }
_jobs: dict[str, dict[str, Any]] = {}


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
    synergy_alpha: float = 0.4  # blend weight: 0.0 = model-only, 1.0 = synergy-only


class ComboPackageOut(BaseModel):
    spellbook_id: str
    produces: list[str]
    cards_included: list[str]
    cards_missing: list[str]
    completion: float
    package_weight: float
    boost_applied: bool = False


class DeckCardOut(CardOut):
    count: int = 1
    is_ramp: bool = False
    roles: list[dict] = []
    score_tags: list[str] = []


class DeckSignalsOut(BaseModel):
    wants_attack: bool = False
    tribal_types: list[str] = []
    real_colors: list[str] = []
    active_boosts: list[str] = []


class DeckOut(BaseModel):
    commander: CardOut
    cards: list[DeckCardOut]
    scores: list[float]
    checkpoint: str
    context_cards: list[str] = []
    proxy_context: bool = False
    combo_packages_triggered: list[ComboPackageOut] = []
    deck_signals: DeckSignalsOut = DeckSignalsOut()


def _save_deck(result: dict) -> str | None:
    """Persist a generated deck to DECK_SAVE_DIR as a timestamped JSON file.

    Returns the filename (not full path) on success, None on failure.
    """
    try:
        DECK_SAVE_DIR.mkdir(parents=True, exist_ok=True)
        commander_name = (
            result.get("commander", {}).get("name", "unknown")
            .replace(" ", "_")
            .replace(",", "")
            .replace("'", "")
        )
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        save_path = DECK_SAVE_DIR / f"{timestamp}_{commander_name}.json"
        save_path.write_text(json.dumps(result, indent=2, default=str))
        log.info("Deck saved: %s", save_path)
        return save_path.name
    except Exception as exc:
        log.warning("Failed to save generated deck: %s", exc)
        return None


class JobStarted(BaseModel):
    job_id: str


class JobStatus(BaseModel):
    job_id: str
    status: str          # queued | running | complete | error
    progress: float      # 0.0 – 1.0
    message: str
    result: dict | None = None
    error: str | None = None


@app.post("/decks/generate", response_model=JobStarted)
async def generate_deck(req: DeckRequest):
    """Submit a deck generation job.  Returns a job_id immediately.

    Poll GET /decks/jobs/{job_id} for progress and the final result.
    """
    job_id = str(uuid4())
    _jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "progress": 0.0,
        "message": "Queued…",
        "result": None,
        "error": None,
        "created_at": datetime.utcnow(),
    }

    async def _run() -> None:
        _jobs[job_id]["status"] = "running"

        def _progress(fraction: float, message: str) -> None:
            _jobs[job_id]["progress"] = fraction
            _jobs[job_id]["message"] = message

        try:
            async with SessionLocal() as db:
                result = await deck_ops.generate(
                    db,
                    req.commander_oracle_id,
                    req.checkpoint,
                    boost_overrides=req.boost_overrides or None,
                    combo_boost=req.combo_boost,
                    partner_oracle_id=req.partner_oracle_id,
                    synergy_alpha=req.synergy_alpha,
                    progress_cb=_progress,
                )
            if result is None:
                _jobs[job_id].update({
                    "status": "error",
                    "error": "Commander not found or model unavailable",
                })
            else:
                deck_filename = _save_deck(result)
                _jobs[job_id].update({
                    "status": "complete",
                    "progress": 1.0,
                    "message": "Done",
                    "result": {**result, "deck_filename": deck_filename},
                })
        except Exception as exc:
            log.exception("Deck generation job %s failed", job_id)
            _jobs[job_id].update({"status": "error", "error": str(exc)})

    asyncio.create_task(_run())
    return JobStarted(job_id=job_id)


@app.get("/decks/jobs/{job_id}", response_model=JobStatus)
async def get_job(job_id: str):
    """Poll for deck generation progress and retrieve the result when complete."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


# ── Generated deck history ───────────────────────────────────────────────────

@app.get("/decks/generated")
async def list_generated_decks(limit: int = 50):
    """List previously generated decks (newest first)."""
    if not DECK_SAVE_DIR.exists():
        return []
    files = sorted(DECK_SAVE_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    result = []
    for f in files[:limit]:
        try:
            data = json.loads(f.read_text())
            result.append({
                "filename": f.name,
                "commander": data.get("commander", {}).get("name", "Unknown"),
                "checkpoint": data.get("checkpoint", ""),
                "card_count": len(data.get("cards", [])),
            })
        except Exception:
            pass
    return result


@app.get("/decks/generated/{filename}")
async def get_generated_deck(filename: str):
    """Retrieve a specific generated deck JSON by filename."""
    safe = Path(filename).name  # prevent path traversal
    if not safe.endswith(".json"):
        raise HTTPException(400, "Invalid filename")
    path = DECK_SAVE_DIR / safe
    if not path.exists():
        raise HTTPException(404, "Deck not found")
    return json.loads(path.read_text())


# ── Deck metrics ─────────────────────────────────────────────────────────────

@app.get("/decks/metrics")
async def deck_metrics():
    """Return cached Recall@K metrics for the current phase3_best checkpoint."""
    if not DATABASE_URL:
        raise HTTPException(503, "DATABASE_URL not configured")

    try:
        from ops import inference
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: inference.recall_at_k(DATABASE_URL, checkpoint_name="phase3_best"),
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


# ── Checkpoint management ─────────────────────────────────────────────────────

@app.get("/checkpoints")
async def list_checkpoints():
    """List available checkpoint files."""
    from ops import inference
    if not inference.CHECKPOINT_DIR.exists():
        return []
    files = sorted(inference.CHECKPOINT_DIR.glob("*.pt"), key=lambda f: f.stat().st_mtime, reverse=True)
    return [
        {
            "name": f.stem,
            "filename": f.name,
            "size_bytes": f.stat().st_size,
        }
        for f in files
    ]


@app.post("/admin/checkpoint")
async def upload_checkpoint(
    file: UploadFile = File(...),
    x_admin_token: str = Header(default=""),
    name: str = "phase3_best",
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


@app.get("/dataset/commanders/info")
async def dataset_commanders_info():
    """Return metadata about the commander training artifact (no auth required)."""
    if not DATASET_CMD_META_PATH.exists():
        raise HTTPException(
            404,
            "No commander dataset available — run: "
            "docker compose run --rm ingest python pipeline.py --stage export_dataset_commanders",
        )
    meta = json.loads(DATASET_CMD_META_PATH.read_text())
    size_bytes = DATASET_CMD_PATH.stat().st_size if DATASET_CMD_PATH.exists() else 0
    return {**meta, "size_bytes": size_bytes}


@app.get("/dataset/commanders/download")
async def dataset_commanders_download():
    """Stream the commander training artifact to the caller (no auth required).

    Contains per-commander synthetic decks derived from pattern decomposition —
    use this for Phase 3 BPR training without human decklists.
    """
    if not DATASET_CMD_PATH.exists():
        raise HTTPException(
            404,
            "No commander dataset available — run: "
            "docker compose run --rm ingest python pipeline.py --stage export_dataset_commanders",
        )
    return FileResponse(
        DATASET_CMD_PATH,
        media_type="application/octet-stream",
        filename="mtg_commanders.pt",
    )
