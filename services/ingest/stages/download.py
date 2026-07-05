"""Download stage — fetch card data and load into the database.

Entrypoint:  python -m stages.download
Orchestrates:
  1. Fetch MTGJSON AtomicCards (or fall back to Scryfall).
  2. Parse and upsert all commander-legal cards into the ``cards`` table.
  3. Import Commander Spellbook combos.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import httpx
from sqlalchemy import text

from stages.db import BATCH_SIZE, Session

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

CACHE_DIR = Path(os.environ.get("MTGJSON_CACHE_DIR", "/data"))

# MTGJSON — primary, no rate limits
MTGJSON_ATOMIC_URL = "https://mtgjson.com/api/v5/AtomicCards.json.gz"
MTGJSON_META_URL   = "https://mtgjson.com/api/v5/Meta.json"

# Scryfall — fallback only
SCRYFALL_BULK_API  = "https://api.scryfall.com/bulk-data"
SCRYFALL_BULK_TYPE = os.environ.get("SCRYFALL_BULK_TYPE", "oracle_cards")


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

    # MTGJSON marks Unfinity sticker-sheet cards commander-"Legal" (upstream
    # bug); isFunny separates them from real eternal-legal Unfinity cards.
    if face.get("isFunny"):
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
        "faces":          faces,                          # all faces (MDFC/split/adventure)
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
        "faces":          card.get("card_faces") or [card],
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
        "faces":          json.dumps(card.get("faces") or []),
    }


def _dedupe_by_oracle_id(cards: list[dict]) -> list[dict]:
    """Collapse duplicate atomic entries that share a scryfallOracleId.

    MTGJSON lists reversible printings (Secret Lair etc.) as separate
    entries with doubled names — "Sol Ring // Sol Ring" alongside
    "Sol Ring" — same oracle_id.  Without dedup, whichever parses last
    wins the ON CONFLICT upsert and clobbers the canonical name, breaking
    every name-based lookup (forced includes, basics, imports).

    Doubled names are collapsed to the single form; on collision the
    entry without " // " in its (original) name is preferred.
    """
    seen: dict[str, dict] = {}
    for card in cards:
        name = card["name"]
        base, sep, rest = name.partition(" // ")
        if sep and base == rest:
            card["name"] = base  # reversible printing, not a real two-face name
        prev = seen.get(card["oracle_id"])
        if prev is None or (" // " in prev["name"] and " // " not in card["name"]):
            seen[card["oracle_id"]] = card
    return list(seen.values())


def _is_commander_legal(card: dict) -> bool:
    """Return True only for cards legal in Commander.

    Filters out Acorn/silver-border cards (no legalities entry), Planes,
    Schemes, Conspiracy cards, and Commander-banned cards (Black Lotus etc.).
    Banned cards are real Magic cards but won't appear in valid decklists.
    """
    return card.get("legalities", {}).get("commander") == "legal"


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

    before = len(cards)
    cards = [c for c in cards if _is_commander_legal(c)]
    log.info("Parsed %d commander-legal cards (dropped %d non-legal)", len(cards), before - len(cards))
    before = len(cards)
    cards = _dedupe_by_oracle_id(cards)
    if len(cards) < before:
        log.info("Deduplicated %d reversible-printing entries", before - len(cards))
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
                        produced_mana, power, toughness, loyalty, scryfall_data, faces
                    ) VALUES (
                        :oracle_id, :name, :mana_cost, :cmc, :type_line, :oracle_text,
                        :colors, :color_identity, :keywords, :legalities,
                        :produced_mana, :power, :toughness, :loyalty, :scryfall_data, :faces
                    )
                    ON CONFLICT (oracle_id) DO UPDATE SET
                        name          = EXCLUDED.name,
                        oracle_text   = EXCLUDED.oracle_text,
                        type_line     = EXCLUDED.type_line,
                        keywords      = EXCLUDED.keywords,
                        legalities    = EXCLUDED.legalities,
                        scryfall_data = EXCLUDED.scryfall_data,
                        faces         = EXCLUDED.faces
                """),
                batch,
            )
            await db.commit()
            log.info("  upserted %d/%d", min(i + BATCH_SIZE, len(cards)), len(cards))


async def import_spellbook_stage() -> None:
    """Import Commander Spellbook combos into combo_packages / combo_package_cards."""
    import import_spellbook
    await import_spellbook.main()


# ── Orchestrator ──────────────────────────────────────────────────────────────

async def download() -> None:
    """Fetch card data + combos and load into the database.

    Run this first (or whenever MTGJSON / Commander Spellbook has new data).
    Does not require embeddings or synergy edges to be present.
    """
    path, source = await fetch_cards()
    await load_cards(path, source)
    await _warn_embedding_drift()
    await import_spellbook_stage()


async def _warn_embedding_drift() -> None:
    """Newly downloaded cards are invisible to model ranking until embedded.

    Standalone `--stage download` runs used to drift silently (~950 cards
    at one point — issue #139); `--stage process` embeds as its next step,
    so the warning is informational there.
    """
    model = os.environ.get(
        "EMBEDDING_MODEL", "sentence-transformers/all-mpnet-base-v2"
    )
    async with Session() as db:
        result = await db.execute(
            text(
                "SELECT count(*) FROM cards c WHERE NOT EXISTS ("
                "  SELECT 1 FROM card_embeddings e"
                "  WHERE e.card_id = c.id AND e.model = :model)"
            ),
            {"model": model},
        )
        missing = result.scalar() or 0
    if missing:
        log.warning(
            "%d cards have no %s embedding — model ranking cannot see them. "
            "Run: pipeline.py --stage embed_cards",
            missing,
            model,
        )


if __name__ == "__main__":
    import asyncio
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(download())
