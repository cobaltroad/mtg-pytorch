"""
MTG ingest pipeline.

Stages
------
1. fetch_cards      – Download card data from MTGJSON (primary); fallback to Scryfall
2. load_cards       – Upsert card rows into the `cards` table
3. embed_cards      – Generate sentence-transformer embeddings → card_embeddings
4. tag_abilities    – Parse keyword / ability tags → card_abilities
5. compute_synergy  – Build pairwise synergy edges → synergy_edges

Data sources
------------
Primary:  MTGJSON bulk downloads (https://mtgjson.com/downloads/)
          No rate limits; full machine-readable dataset.
Fallback: Scryfall oracle_cards bulk JSON — only used if MTGJSON unavailable,
          because Scryfall enforces strict rate limits on their API.

Run all stages:   python pipeline.py
Run one stage:    python pipeline.py --stage embed_cards
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
from pathlib import Path

import httpx
import numpy as np
from sentence_transformers import SentenceTransformer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from tqdm import tqdm

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATABASE_URL = os.environ["DATABASE_URL"]
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
CACHE_DIR = Path(os.environ.get("EDHREC_CACHE_DIR", "/data"))
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "256"))

# MTGJSON — primary, no rate limits
# AtomicCards.json contains one entry per unique oracle card, ~30 MB compressed
MTGJSON_ATOMIC_URL = "https://mtgjson.com/api/v5/AtomicCards.json.gz"
MTGJSON_META_URL   = "https://mtgjson.com/api/v5/Meta.json"

# Scryfall — fallback only
SCRYFALL_BULK_API  = "https://api.scryfall.com/bulk-data"
SCRYFALL_BULK_TYPE = os.environ.get("SCRYFALL_BULK_TYPE", "oracle_cards")

engine = create_async_engine(DATABASE_URL, echo=False)
Session = async_sessionmaker(engine, expire_on_commit=False)


# ── Stage 1: Fetch ────────────────────────────────────────────────────────────

async def _fetch_mtgjson() -> Path | None:
    """Download AtomicCards.json from MTGJSON. Returns path or None on failure."""
    import gzip
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest_gz  = CACHE_DIR / "mtgjson_AtomicCards.json.gz"
    dest     = CACHE_DIR / "mtgjson_AtomicCards.json"

    # Check if we already have a fresh copy by comparing meta version
    meta_file = CACHE_DIR / "mtgjson_meta.json"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            meta = (await client.get(MTGJSON_META_URL)).json()
        current_version = meta.get("data", {}).get("version", "")
        if dest.exists() and meta_file.exists():
            cached_version = json.loads(meta_file.read_text()).get("version", "")
            if cached_version == current_version:
                log.info("MTGJSON cache is current (version %s)", current_version)
                return dest
        meta_file.write_text(json.dumps({"version": current_version}))
    except Exception as e:
        log.warning("Could not check MTGJSON meta: %s", e)
        if dest.exists():
            log.info("Using existing MTGJSON cache")
            return dest

    log.info("Downloading MTGJSON AtomicCards…")
    try:
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            async with client.stream("GET", MTGJSON_ATOMIC_URL) as r:
                r.raise_for_status()
                with dest_gz.open("wb") as f:
                    async for chunk in r.aiter_bytes(65536):
                        f.write(chunk)

        log.info("Decompressing…")
        with gzip.open(dest_gz, "rb") as gz, dest.open("wb") as out:
            out.write(gz.read())
        dest_gz.unlink(missing_ok=True)
        log.info("MTGJSON saved to %s", dest)
        return dest
    except Exception as e:
        log.error("MTGJSON download failed: %s", e)
        return None


async def _fetch_scryfall_fallback() -> Path:
    """Fallback: download Scryfall oracle_cards bulk JSON."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = CACHE_DIR / f"scryfall_{SCRYFALL_BULK_TYPE}.json"
    if dest.exists():
        log.info("Scryfall cache hit: %s", dest)
        return dest

    log.info("Fetching Scryfall bulk-data manifest (fallback)…")
    async with httpx.AsyncClient(timeout=30) as client:
        manifest = (await client.get(SCRYFALL_BULK_API)).json()
        entry = next(e for e in manifest["data"] if e["type"] == SCRYFALL_BULK_TYPE)
        log.info("Downloading %s (%.0f MB)…", entry["download_uri"], entry["size"] / 1e6)
        async with client.stream("GET", entry["download_uri"]) as r:
            with dest.open("wb") as f:
                async for chunk in r.aiter_bytes(65536):
                    f.write(chunk)

    log.info("Scryfall saved to %s", dest)
    return dest


async def fetch_cards() -> tuple[Path, str]:
    """Return (path, source) where source is 'mtgjson' or 'scryfall'."""
    path = await _fetch_mtgjson()
    if path:
        return path, "mtgjson"
    log.warning("Falling back to Scryfall")
    path = await _fetch_scryfall_fallback()
    return path, "scryfall"


# ── Stage 2: Load cards ───────────────────────────────────────────────────────

def _normalise_mtgjson(name: str, faces: list[dict]) -> dict | None:
    """Convert MTGJSON AtomicCards entry to our canonical card dict."""
    # AtomicCards groups faces under the card name; take face[0] as primary
    face = faces[0]

    # MTGJSON uses "identifiers.scryfallOracleId" for the oracle UUID
    oracle_id = (face.get("identifiers") or {}).get("scryfallOracleId")
    if not oracle_id:
        return None

    # Legalities: MTGJSON uses {format: "Legal"/"Banned"/etc}
    legalities_raw = face.get("legalities") or {}
    legalities = {fmt: status.lower() for fmt, status in legalities_raw.items()}

    # Color identity: list of single-char color codes e.g. ["W", "U"]
    color_identity = face.get("colorIdentity") or []
    colors = face.get("colors") or []
    keywords = face.get("keywords") or []

    return {
        "oracle_id":      oracle_id,
        "name":           name,
        "mana_cost":      face.get("manaCost"),
        "cmc":            face.get("manaValue"),          # MTGJSON calls it manaValue
        "type_line":      face.get("type"),
        "oracle_text":    face.get("text"),
        "colors":         colors,
        "color_identity": color_identity,
        "keywords":       keywords,
        "legalities":     legalities,
        "produced_mana":  face.get("producedMana") or [],
        "power":          face.get("power"),
        "toughness":      face.get("toughness"),
        "loyalty":        face.get("loyalty"),
        "scryfall_data":  face,                           # store raw face for reference
    }


def _normalise_scryfall(card: dict) -> dict | None:
    if not card.get("oracle_id"):
        return None
    legalities = card.get("legalities") or {}
    return {
        "oracle_id":      card["oracle_id"],
        "name":           card.get("name"),
        "mana_cost":      card.get("mana_cost"),
        "cmc":            card.get("cmc"),
        "type_line":      card.get("type_line"),
        "oracle_text":    card.get("oracle_text"),
        "colors":         card.get("colors") or [],
        "color_identity": card.get("color_identity") or [],
        "keywords":       card.get("keywords") or [],
        "legalities":     legalities,
        "produced_mana":  card.get("produced_mana") or [],
        "power":          card.get("power"),
        "toughness":      card.get("toughness"),
        "loyalty":        card.get("loyalty"),
        "scryfall_data":  card,
    }


def _to_row(card: dict) -> dict:
    return {
        "oracle_id":      card["oracle_id"],
        "name":           card["name"],
        "mana_cost":      card.get("mana_cost"),
        "cmc":            card.get("cmc"),
        "type_line":      card.get("type_line"),
        "oracle_text":    card.get("oracle_text"),
        "colors":         card.get("colors") or [],
        "color_identity": card.get("color_identity") or [],
        "keywords":       card.get("keywords") or [],
        "legalities":     json.dumps(card.get("legalities") or {}),
        "produced_mana":  card.get("produced_mana") or [],
        "power":          card.get("power"),
        "toughness":      card.get("toughness"),
        "loyalty":        card.get("loyalty"),
        "scryfall_data":  json.dumps(card.get("scryfall_data") or {}),
    }


def _parse_cards(path: Path, source: str) -> list[dict]:
    log.info("Parsing %s from %s…", source, path)
    with path.open() as f:
        raw = json.load(f)

    cards = []
    if source == "mtgjson":
        data = raw.get("data", raw)  # AtomicCards wraps in {"data": {...}}
        for name, faces in data.items():
            card = _normalise_mtgjson(name, faces if isinstance(faces, list) else [faces])
            if card:
                cards.append(card)
    else:
        cards = [_normalise_scryfall(c) for c in raw if isinstance(c, dict)]
        cards = [c for c in cards if c]

    log.info("Parsed %d cards", len(cards))
    return cards


async def load_cards(path: Path, source: str) -> None:
    cards = _parse_cards(path, source)
    log.info("Upserting %d cards…", len(cards))
    async with Session() as db:
        for i in range(0, len(cards), BATCH_SIZE):
            batch = [_to_row(c) for c in cards[i : i + BATCH_SIZE]]
            await db.execute(
                text("""
                    INSERT INTO cards (
                        oracle_id, name, mana_cost, cmc, type_line, oracle_text,
                        colors, color_identity, keywords, legalities,
                        produced_mana, power, toughness, loyalty, scryfall_data
                    ) VALUES (
                        :oracle_id, :name, :mana_cost, :cmc, :type_line, :oracle_text,
                        :colors, :color_identity, :keywords, :legalities,
                        :produced_mana, :power, :toughness, :loyalty, :scryfall_data
                    )
                    ON CONFLICT (oracle_id) DO UPDATE SET
                        name          = EXCLUDED.name,
                        oracle_text   = EXCLUDED.oracle_text,
                        type_line     = EXCLUDED.type_line,
                        keywords      = EXCLUDED.keywords,
                        legalities    = EXCLUDED.legalities,
                        scryfall_data = EXCLUDED.scryfall_data
                """),
                batch,
            )
            await db.commit()
            log.info("  upserted %d/%d", min(i + BATCH_SIZE, len(cards)), len(cards))


# ── Stage 3: Embed ────────────────────────────────────────────────────────────

def _card_text(row) -> str:
    parts = [row[1]]  # name
    if row[4]:         # type_line
        parts.append(row[4])
    if row[5]:         # oracle_text
        parts.append(row[5])
    return " | ".join(parts)


async def embed_cards() -> None:
    log.info("Loading embedding model: %s", EMBEDDING_MODEL)
    model = SentenceTransformer(EMBEDDING_MODEL)

    async with Session() as db:
        result = await db.execute(text("""
            SELECT c.id, c.name, c.oracle_id, c.mana_cost, c.type_line, c.oracle_text
            FROM cards c
            WHERE NOT EXISTS (
                SELECT 1 FROM card_embeddings e
                WHERE e.card_id = c.id AND e.model = :model
            )
        """), {"model": EMBEDDING_MODEL})
        rows = result.fetchall()

    log.info("Embedding %d cards (batch=%d)…", len(rows), BATCH_SIZE)
    for i in tqdm(range(0, len(rows), BATCH_SIZE)):
        batch = rows[i : i + BATCH_SIZE]
        texts = [_card_text(r) for r in batch]
        embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)

        async with Session() as db:
            for row, emb in zip(batch, embeddings):
                await db.execute(text("""
                    INSERT INTO card_embeddings (card_id, model, embedding)
                    VALUES (:card_id, :model, :emb)
                    ON CONFLICT (card_id, model) DO NOTHING
                """), {
                    "card_id": str(row[0]),
                    "model": EMBEDDING_MODEL,
                    "emb": "[" + ",".join(str(x) for x in emb.tolist()) + "]",
                })
            await db.commit()


# ── Stage 4: Tag abilities ────────────────────────────────────────────────────

TRIGGER_PATTERNS: list[tuple[str, str, str]] = [
    (r"when(ever)?\s+.{0,60}enters", "ETB trigger", "enters_battlefield"),
    (r"when(ever)?\s+.{0,60}dies", "Dies trigger", "dies"),
    (r"when(ever)?\s+.{0,60}attacks", "Attack trigger", "attacks"),
    (r"when(ever)?\s+you (draw|cast|play)", "Spell/Draw trigger", "spell_or_draw"),
    (r"at the beginning of (your|each player's|each)?\s*(upkeep|combat|end step)", "Phase trigger", "phase_begin"),
    (r"tap\s*:", "Tap ability", "activated_tap"),
    (r"sacrifice .{0,40}:", "Sacrifice ability", "activated_sacrifice"),
    (r"\{t\}\s*:", "Tap ability", "activated_tap"),
]

KEYWORD_RE = re.compile(
    r"\b(flying|trample|haste|vigilance|deathtouch|lifelink|reach|hexproof|"
    r"indestructible|flash|first strike|double strike|menace|prowess|"
    r"ward|protection|shroud|defender|annihilator|cascade|convoke|"
    r"delve|exploit|fabricate|flashback|kicker|madness|miracle|"
    r"morph|overload|persist|proliferate|rebound|replicate|retrace|"
    r"scry|storm|suspend|threshold|undying|unearth|wither)\b",
    re.IGNORECASE,
)


async def tag_abilities() -> None:
    log.info("Tagging abilities…")
    async with Session() as db:
        result = await db.execute(text("""
            SELECT c.id, c.oracle_text, c.keywords
            FROM cards c
            WHERE NOT EXISTS (
                SELECT 1 FROM card_abilities a WHERE a.card_id = c.id
            )
            AND c.oracle_text IS NOT NULL
        """))
        rows = result.fetchall()

    log.info("Tagging %d cards…", len(rows))
    async with Session() as db:
        for row in tqdm(rows):
            card_id, oracle_text, kw_list = row[0], row[1] or "", row[2] or []
            inserts = []

            for kw in kw_list:
                inserts.append({
                    "card_id": str(card_id),
                    "ability_type": "keyword",
                    "ability_name": kw,
                    "trigger_event": None,
                    "effect_class": None,
                    "raw_text": kw,
                })

            for pattern, name, event in TRIGGER_PATTERNS:
                m = re.search(pattern, oracle_text, re.IGNORECASE)
                if m:
                    inserts.append({
                        "card_id": str(card_id),
                        "ability_type": "triggered" if "trigger" in name else "activated",
                        "ability_name": name,
                        "trigger_event": event,
                        "effect_class": None,
                        "raw_text": m.group(0),
                    })

            for m in KEYWORD_RE.finditer(oracle_text):
                kw = m.group(0).lower()
                if kw not in [k.lower() for k in kw_list]:
                    inserts.append({
                        "card_id": str(card_id),
                        "ability_type": "keyword",
                        "ability_name": kw.title(),
                        "trigger_event": None,
                        "effect_class": None,
                        "raw_text": kw,
                    })

            if inserts:
                await db.execute(text("""
                    INSERT INTO card_abilities
                        (card_id, ability_type, ability_name, trigger_event, effect_class, raw_text)
                    VALUES
                        (:card_id, :ability_type, :ability_name, :trigger_event, :effect_class, :raw_text)
                """), inserts)

        await db.commit()


# ── Stage 5: Synergy edges ────────────────────────────────────────────────────

async def compute_synergy() -> None:
    log.info("Computing ability-trigger synergy edges…")

    PRODUCER_MAP = {
        "enters_battlefield": ["token", "create", "put", "enters"],
        "dies": ["dies", "graveyard", "sacrifice"],
        "attacks": ["attack", "combat"],
        "spell_or_draw": ["draw", "cast", "spell"],
        "phase_begin": ["upkeep", "end step", "combat"],
    }

    async with Session() as db:
        for trigger_event, producer_keywords in PRODUCER_MAP.items():
            consumers = (await db.execute(text("""
                SELECT card_id FROM card_abilities WHERE trigger_event = :te
            """), {"te": trigger_event})).fetchall()

            if not consumers:
                continue

            like_clauses = " OR ".join(
                f"lower(oracle_text) LIKE '%{kw}%'" for kw in producer_keywords
            )
            producers = (await db.execute(text(f"""
                SELECT id FROM cards WHERE {like_clauses}
            """))).fetchall()

            edges = []
            for prod in producers:
                for cons in consumers:
                    if prod[0] == cons[0]:
                        continue
                    edges.append({
                        "card_a": str(prod[0]),
                        "card_b": str(cons[0]),
                        "score_type": "ability_trigger",
                        "score": 1.0,
                        "metadata": json.dumps({"trigger_event": trigger_event}),
                    })

            for i in range(0, len(edges), BATCH_SIZE):
                await db.execute(text("""
                    INSERT INTO synergy_edges (card_a, card_b, score_type, score, metadata)
                    VALUES (:card_a, :card_b, :score_type, :score, :metadata)
                    ON CONFLICT (card_a, card_b, score_type) DO NOTHING
                """), edges[i : i + BATCH_SIZE])
            await db.commit()
            log.info("  %s → %d edges", trigger_event, len(edges))


# ── Entry point ───────────────────────────────────────────────────────────────

async def run_all():
    path, source = await fetch_cards()
    await load_cards(path, source)
    await embed_cards()
    await tag_abilities()
    await compute_synergy()


async def _load_cards_stage():
    path, source = await fetch_cards()
    await load_cards(path, source)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stage",
        choices=["fetch_cards", "load_cards", "embed_cards", "tag_abilities", "compute_synergy"],
        default=None,
    )
    args = parser.parse_args()

    if args.stage == "fetch_cards":
        asyncio.run(fetch_cards())
    elif args.stage == "load_cards":
        asyncio.run(_load_cards_stage())
    elif args.stage == "embed_cards":
        asyncio.run(embed_cards())
    elif args.stage == "tag_abilities":
        asyncio.run(tag_abilities())
    elif args.stage == "compute_synergy":
        asyncio.run(compute_synergy())
    else:
        asyncio.run(run_all())
