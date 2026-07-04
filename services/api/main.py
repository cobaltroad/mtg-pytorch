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
from ops import cards as card_ops, synergy as synergy_ops, training as training_ops

log = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
DATASET_PATH = Path(os.environ.get("DATASET_PATH", "/data/mtg_dataset.pt"))
DATASET_META_PATH = Path(os.environ.get("DATASET_META_PATH", "/data/mtg_dataset.json"))
DATASET_CMD_PATH = Path(os.environ.get("DATASET_CMD_PATH", "/data/mtg_commanders.pt"))
DATASET_CMD_META_PATH = Path(
    os.environ.get("DATASET_CMD_META_PATH", "/data/mtg_commanders.json")
)
DECK_SAVE_DIR = Path(os.environ.get("DECK_SAVE_DIR", "/app/generated_decks"))

app = FastAPI(
    title="MTG Commander AI",
    version="0.1.0",
    description="Card similarity search, synergy queries, and commander deck scoring.",
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


# ── Commander decomposition ───────────────────────────────────────────────────

# Producer → deck-key translation and labels.
# Canonical source: services/ingest/synergy/commander_mechanics.py
# Keep this in sync with PRODUCER_DECOMPOSE_TO_DECK_KEY and DECK_KEY_LABELS there.
_PRODUCER_DECOMPOSE_TO_DECK_KEY: dict[str, list[str]] = {
    "lifegain_producer": ["lifegain_trigger"],
    "draw_producer": ["draw_trigger"],
    "counter_placement": ["counter_trigger"],
    "high_mv_payoff": ["high_mv_payoff"],
    "cascade": ["cast_from_exile_payoff"],
    "creature_token_generator": ["creature_etb_payoff", "sac_outlet"],
    "attack_trigger": ["combat_tricks"],
    "combat_damage_to_player": ["combat_tricks"],
    "death_trigger": [
        "sac_outlet",
        "sacrifice_fodder",
        "toughness_1_creatures",
        "destroy_removal",
        "damage_removal",
    ],
    "sacrifice_payoff": ["sac_outlet", "treasure_generators", "token_generators"],
    "cast_trigger_enchantment": ["enchantment_cast", "spell_enchantment"],
    "cast_trigger_creature": ["creature_cast", "spell_creature"],
    "cast_trigger_artifact": ["artifact_cast", "spell_artifact"],
    "cast_trigger_instant_sorcery": ["instant_sorcery_cast", "spell_instant_sorcery"],
    "cast_trigger_historic": ["historic_cast", "spell_historic"],
    "cast_trigger_aura_equipment": ["aura_equipment_cast", "spell_aura_equipment"],
    **{f"cast_trigger_{c}": [f"spell_{c}"]
       for c in ("white", "blue", "black", "red", "green", "colorless")},
}

# All consumer decompose keys (deck key == decompose key for consumers).
_CONSUMER_DECOMPOSE_KEYS: frozenset[str] = frozenset(
    {
        "creature_token_generator",
        "mana_dork",
        # tribal
        "tribal_elf",
        "tribal_dragon",
        "tribal_zombie",
        "tribal_vampire",
        "tribal_eldrazi",
        "tribal_human",
        "tribal_dinosaur",
        "tribal_goblin",
        "tribal_angel",
        "tribal_pirate",
        "tribal_wizard",
        "tribal_assassin",
        "tribal_merfolk",
        "tribal_cat",
        "tribal_sliver",
        "tribal_wolf",
        "tribal_demon",
        "tribal_ninja",
        "tribal_squirrel",
        "tribal_elemental",
        "tribal_dog",
        "tribal_spirit",
        "tribal_knight",
        "tribal_horror",
        "tribal_faerie",
        "tribal_dwarf",
    }
)

_DECK_KEY_LABELS: dict[str, str] = {
    # producer deck keys
    "lifegain_trigger": "Lifegain trigger payoffs",
    "draw_trigger": "Draw trigger payoffs",
    "counter_trigger": "Counter trigger payoffs",
    "high_mv_payoff": "High mana value payoffs",
    "cast_from_exile_payoff": "Cast-from-exile payoffs",
    "creature_etb_payoff": "Creature ETB payoffs",
    # translated consumer deck keys
    "combat_tricks": "Combat tricks (evasion, pump, haste)",
    "sac_outlet": "Sac outlets",
    "sacrifice_fodder": "Self-sacrificing fodder",
    "toughness_1_creatures": "Toughness-1 creatures",
    "destroy_removal": "Destroy removal",
    "damage_removal": "Damage-based removal",
    "treasure_generators": "Treasure generators",
    "token_generators": "Token generators",
    # cast trigger amplifiers
    "enchantment_cast": "Enchantment cast trigger amplifiers",
    "creature_cast": "Creature cast trigger amplifiers",
    "artifact_cast": "Artifact cast trigger amplifiers",
    "instant_sorcery_cast": "Instant/sorcery cast trigger amplifiers",
    "historic_cast": "Historic cast trigger amplifiers",
    "aura_equipment_cast": "Aura/equipment cast trigger amplifiers",
    # spell fodder
    "spell_enchantment": "Enchantment spells",
    "spell_creature": "Creature spells",
    "spell_artifact": "Artifact spells",
    "spell_instant_sorcery": "Instant / sorcery spells",
    "spell_historic": "Historic spells",
    "spell_aura_equipment": "Aura / equipment spells",
    "mana_dork": "Mana ability creatures",
    "trigger_doubling": "Creatures with attack-triggered abilities",
    "token_generator": "Token doublers and token creation payoffs",
    "artifact_token_generator": "Artifact ETB and graveyard payoffs (non-creature tokens)",
    "proliferate_matters": "Counter-bearing permanents (proliferate targets)",
    "spell_white": "White spells",
    "spell_blue": "Blue spells",
    "spell_black": "Black spells",
    "spell_red": "Red spells",
    "spell_green": "Green spells",
    "spell_colorless": "Colorless spells",
    **{
        f"tribal_{t}": f"{t.title()} tribal creatures"
        for t in (
            "elf",
            "dragon",
            "zombie",
            "vampire",
            "eldrazi",
            "human",
            "dinosaur",
            "goblin",
            "angel",
            "pirate",
            "wizard",
            "assassin",
            "merfolk",
            "cat",
            "sliver",
            "wolf",
            "demon",
            "ninja",
            "squirrel",
            "elemental",
            "dog",
            "spirit",
            "knight",
            "horror",
            "faerie",
            "dwarf",
        )
    },
}


class DecomposeSignal(BaseModel):
    ability_name: str  # human-readable label, e.g. "Attack trigger"
    trigger_event: str  # decompose key, e.g. "attack_trigger"
    raw_text: str | None = None  # matched oracle phrase
    deck_keys: list[str] = []  # what the deck needs, e.g. ["counter_trigger"]
    deck_labels: list[str] = []  # e.g. ["Counter trigger payoffs"]
    side: str | None = None  # "producer" | "consumer" | None


def _enrich_signal(
    ability_name: str, trigger_event: str, raw_text: str | None
) -> DecomposeSignal:
    """Resolve deck_keys, deck_labels, and side for a decompose signal."""
    key = trigger_event or ""
    if key in _PRODUCER_DECOMPOSE_TO_DECK_KEY:
        deck_keys = _PRODUCER_DECOMPOSE_TO_DECK_KEY[key]
        side = "producer"
    elif key in _CONSUMER_DECOMPOSE_KEYS:
        deck_keys = [key]
        side = "consumer"
    else:
        deck_keys = []
        side = None
    deck_labels = [_DECK_KEY_LABELS.get(dk, dk) for dk in deck_keys]
    return DecomposeSignal(
        ability_name=ability_name,
        trigger_event=key,
        raw_text=raw_text,
        deck_keys=deck_keys,
        deck_labels=deck_labels,
        side=side,
    )


@app.get("/commanders/{oracle_id}/decompose", response_model=list[DecomposeSignal])
async def get_commander_decompose(
    oracle_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Return decompose pipeline signals for a commander (source='decompose' card_abilities rows)."""
    result = await db.execute(
        text("""
            SELECT ca.ability_name, ca.trigger_event, ca.raw_text
            FROM card_abilities ca
            JOIN cards c ON c.id = ca.card_id
            WHERE c.oracle_id = :oid
              AND ca.source = 'decompose'
              AND ca.ability_name NOT LIKE '%(XMage)%'
            ORDER BY ca.ability_type, ca.ability_name
        """),
        {"oid": str(oracle_id)},
    )
    return [_enrich_signal(row[0], row[1] or "", row[2]) for row in result.fetchall()]


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


# ── Commander candidate scoring ───────────────────────────────────────────────


class CandidateOut(BaseModel):
    oracle_id: UUID
    name: str
    type_line: str | None = None
    mana_cost: str | None = None
    cmc: float | None = None
    score: float  # CommanderScorer fit (Phase 3)
    cosine_sim: float  # Phase 2 encoder cosine similarity
    tags: list[
        str
    ] = []  # role tags + xmage trigger tags; xmage tags prefixed with "⚡"


@app.get("/commanders/{oracle_id}/candidates", response_model=list[CandidateOut])
async def score_commander_candidates(
    oracle_id: UUID,
    checkpoint: str = "phase3_best",
    db: AsyncSession = Depends(get_db),
):
    """Score all color-identity-legal cards against a commander.

    Returns all candidates sorted by Phase 3 CommanderScorer output —
    the model's learned estimate of commander-card fit.  No heuristic
    adjustments applied.
    """
    if not DATABASE_URL:
        raise HTTPException(503, "DATABASE_URL not configured")

    result = await db.execute(
        text(
            "SELECT id::text, color_identity FROM cards WHERE oracle_id = :oid LIMIT 1"
        ),
        {"oid": str(oracle_id)},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(404, "Commander not found")
    commander_id, color_identity = row[0], frozenset(row[1] or [])

    from ops import inference

    loop = asyncio.get_event_loop()

    model = await loop.run_in_executor(None, inference.get_model, checkpoint)
    if model is None:
        raise HTTPException(
            503, f"Model checkpoint '{checkpoint}' unavailable or dimension mismatch"
        )

    embeddings = await loop.run_in_executor(
        None, inference.get_embeddings, DATABASE_URL
    )
    card_meta = await loop.run_in_executor(
        None, inference.get_card_metadata, DATABASE_URL
    )

    decomposed_rows = await db.execute(
        text(
            "SELECT card_b::text FROM synergy_edges"
            " WHERE score_type = 'decomposed_candidates' AND card_a = CAST(:cmd_id AS uuid)"
        ),
        {"cmd_id": commander_id},
    )
    candidates = [
        row[0]
        for row in decomposed_rows.fetchall()
        if row[0] in embeddings and row[0] != commander_id
    ]

    if not candidates:
        raise HTTPException(
            503,
            "No decomposed_candidates found for this commander. "
            "Run pipeline.py --stage decompose_commanders then "
            "--stage compute_commander_value_synergy first.",
        )

    scored = await loop.run_in_executor(
        None,
        lambda: inference.score_candidates(
            commander_id, candidates, embeddings, model
        ),
    )

    out = []
    for cid, score, cosine_sim in scored:
        meta = card_meta.get(cid)
        if meta:
            out.append(
                CandidateOut(
                    score=round(score, 6),
                    cosine_sim=round(cosine_sim, 6),
                    **meta,
                )
            )

    # Fetch role + xmage trigger tags for all returned candidates in one query.
    # Role tags are plain (e.g. "ramp"); xmage tags are prefixed with "⚡" to
    # distinguish their source visually in the UI.
    if out:
        oracle_ids = [str(c.oracle_id) for c in out]
        tag_rows = await db.execute(
            text("""
                SELECT c.oracle_id::text,
                       ca.ability_type,
                       ca.ability_name,
                       ca.trigger_event,
                       ca.source
                FROM card_abilities ca
                JOIN cards c ON c.id = ca.card_id
                WHERE c.oracle_id = ANY(CAST(:oracle_ids AS uuid[]))
                  AND (
                    ca.ability_type = 'role'
                    OR ca.source    = 'xmage'
                  )
            """),
            {"oracle_ids": oracle_ids},
        )
        # Build oracle_id → sorted tag list
        from collections import defaultdict

        tags_by_oracle: dict[str, list[str]] = defaultdict(list)
        seen_tags: dict[str, set[str]] = defaultdict(set)
        for oid, ability_type, ability_name, trigger_event, source in tag_rows.fetchall():
            if source == "xmage":
                tag = f"⚡ {trigger_event or ability_name}"
            else:
                tag = trigger_event or ability_name
            if tag not in seen_tags[oid]:
                seen_tags[oid].add(tag)
                tags_by_oracle[oid].append(tag)

        # Attach tags to each candidate; role tags first, then xmage tags
        for candidate in out:
            oid = str(candidate.oracle_id)
            raw = tags_by_oracle.get(oid, [])
            candidate.tags = sorted(
                [t for t in raw if not t.startswith("⚡")],
            ) + sorted(
                [t for t in raw if t.startswith("⚡")],
            )

    return out


# ── Composition-first deck building (docs/composition-first-plan.md W5) ──────


@app.post("/commanders/{oracle_id}/build")
async def build_commander_deck(
    oracle_id: UUID,
    ranking: str = Query("model", pattern="^(model|heuristic)$"),
    goldfish_games: int = Query(400, ge=50, le=2000),
    db: AsyncSession = Depends(get_db),
):
    """Build a full 99-card deck with the composition engine.

    Deterministic quotas (lands/ramp/draw/interaction/protection) are
    derived from the commander; the Phase 1/2 models only rank candidates
    within each quota when ranking='model'.  Returns the deck plus the
    quota profile with per-value rationales, goldfish castability metrics,
    and the saved deck_filename (also visible under /decks/generated).
    """
    from ops import composition

    try:
        return await composition.build_commander_deck(
            db,
            str(oracle_id),
            ranking=ranking,
            goldfish_games=goldfish_games,
            save_dir=DECK_SAVE_DIR,
        )
    except LookupError as e:
        raise HTTPException(404, str(e))
    except RuntimeError as e:
        raise HTTPException(503, str(e))


# ── Generated deck history ───────────────────────────────────────────────────


@app.get("/decks/generated")
async def list_generated_decks(limit: int = 50):
    """List previously generated decks (newest first)."""
    if not DECK_SAVE_DIR.exists():
        return []
    files = sorted(
        DECK_SAVE_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True
    )
    result = []
    for f in files[:limit]:
        try:
            data = json.loads(f.read_text())
            result.append(
                {
                    "filename": f.name,
                    "commander": data.get("commander", {}).get("name", "Unknown"),
                    "checkpoint": data.get("checkpoint", ""),
                    "card_count": len(data.get("cards", [])),
                }
            )
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


def _is_scorer_checkpoint(path) -> bool:
    """Return True only if the checkpoint contains a CommanderScorer state dict.

    CommanderScorer.net.0 is Linear(embed_dim * 2, embed_dim), so its weight
    has shape (embed_dim, embed_dim * 2) — i.e. dim1 == 2 * dim0.

    BilinearSynergyHead checkpoints have keys like "W.0", "W.1", …
    CardEncoder checkpoints have net.0.weight with a large input dim (768).
    Neither should appear in the scorer dropdown.
    """
    try:
        import torch
        state = torch.load(path, map_location="cpu", weights_only=True)
        w = state.get("net.0.weight")
        return w is not None and w.shape[1] == w.shape[0] * 2
    except Exception:
        return False


@app.get("/checkpoints")
async def list_checkpoints():
    """List available CommanderScorer checkpoint files.

    Only checkpoints whose state dict matches the CommanderScorer architecture
    are returned.  Encoder checkpoints (phase1_best, phase2_best) and the
    bilinear head (phase2_bilinear_best) are excluded — selecting them as a
    scorer would cause a 500 error.
    """
    from ops import inference

    if not inference.CHECKPOINT_DIR.exists():
        return []
    files = sorted(
        inference.CHECKPOINT_DIR.glob("*.pt"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    return [
        {
            "name": f.stem,
            "filename": f.name,
            "size_bytes": f.stat().st_size,
        }
        for f in files
        if _is_scorer_checkpoint(f)
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
    tmp = dest.with_suffix(".pt.tmp")

    try:
        data = await file.read()
        tmp.write_bytes(data)
        tmp.replace(dest)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise HTTPException(500, f"Write failed: {exc}")

    # Evict cached model(s) so next inference loads the new weights.
    # Uploading phase2_best invalidates ALL bundles (each bundle embeds the encoder).
    if name == "phase2_best":
        inference._model_cache.clear()
        log.info(
            "phase2_best replaced — full model cache cleared (%d bytes)", len(data)
        )
    else:
        inference._model_cache.pop(name, None)
        log.info(
            "Checkpoint uploaded and cache cleared: %s (%d bytes)", dest, len(data)
        )
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
