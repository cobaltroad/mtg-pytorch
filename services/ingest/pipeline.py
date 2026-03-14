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
    # ── Tightened existing ────────────────────────────────────────────────────
    # ETB subtypes — checked before the generic ETB catch-all so the most specific wins
    (r"when(ever)?\s+(a |another )?nontoken creature.{0,30}enters the battlefield", "Nontoken creature ETB", "nontoken_etb"),
    (r"when(ever)?\s+(a |another )?creature.{0,30}enters the battlefield", "Creature ETB trigger", "creature_etb"),
    (r"when(ever)?\s+(a |an )?artifact.{0,30}enters the battlefield", "Artifact ETB trigger", "artifact_etb"),
    # Generic ETB catch-all (also catches enchantments, planeswalkers, etc.)
    (r"when(ever)?\s+.{0,60}enters the battlefield", "ETB trigger", "enters_battlefield"),
    # Nontoken creature dies: the most common Aristocrats / sacrifice payoff template
    (r"when(ever)?\s+(a |another )?nontoken creature.{0,30}dies", "Nontoken dies trigger", "nontoken_dies"),
    # Generic dies trigger (any creature)
    (r"when(ever)?\s+.{0,60}dies", "Dies trigger", "dies"),
    (r"when(ever)?\s+.{0,60}attacks", "Attack trigger", "attacks"),
    # Split spell_or_draw into two precise events
    (r"when(ever)?\s+(you |a player )?draw", "Draw trigger", "draw"),
    (r"when(ever)?\s+(you |a player |an opponent )cast.{0,10}(noncreature|instant or sorcery|a spell)", "Cast trigger", "spell_cast"),
    # Phase: drop "combat" (ambiguous with attacks trigger)
    (r"at the beginning of (your|each player's|each)?\s*(upkeep|end step)", "Phase trigger", "phase_begin"),

    # ── New trigger types ─────────────────────────────────────────────────────
    (r"when(ever)?\s+(you |a player )?gain(s)? life", "Lifegain trigger", "lifegain"),
    (r"when(ever)?\s+a land enters", "Landfall trigger", "landfall"),
    (r"when(ever)?\s+(you |a player |an opponent )discard", "Discard trigger", "discard"),
    (r"when(ever)?\s+(you )?create.{0,30}token", "Token creation trigger", "token_creation"),
    (r"when(ever)?\s+.{0,40}(counter|counters).{0,20}(placed|put) on", "Counter trigger", "counter_added"),
    (r"when(ever)?\s+.{0,50}deals? (combat )?damage to (a player|an opponent|you)", "Combat damage trigger", "combat_damage"),
    (r"when(ever)?\s+(you )?sacrifice", "Sacrifice trigger", "sacrifice"),

    # ── Activated abilities (unchanged) ───────────────────────────────────────
    (r"\{t\}\s*:", "Tap ability", "activated_tap"),
    (r"sacrifice .{0,40}:", "Sacrifice activated", "activated_sacrifice"),
]

# Major Commander tribes, in rough popularity order.
# For each tribe we generate two trigger patterns:
#   tribal_{tribe}_cast — "whenever you cast a {Tribe} spell"
#   tribal_{tribe}_etb  — "whenever a {Tribe} enters (the battlefield)"
TRIBES: list[str] = [
    "Dragon", "Elf", "Zombie", "Vampire", "Eldrazi", "Human",
    "Dinosaur", "Goblin", "Angel", "Pirate", "Wizard", "Assassin",
    "Merfolk", "Cat", "Sliver",
]

for _tribe in TRIBES:
    _t = _tribe.lower()
    TRIGGER_PATTERNS.append((
        rf"when(ever)?\s+you cast (a |an )?{_tribe}",
        f"{_tribe} cast trigger",
        f"tribal_{_t}_cast",
    ))
    TRIGGER_PATTERNS.append((
        rf"when(ever)?\s+(a |another )?{_tribe}.{{0,20}}enters",
        f"{_tribe} ETB trigger",
        f"tribal_{_t}_etb",
    ))
    # Lord / anthem effect: "(other) {Tribe}s you control get +1/+1" — static ability
    # Treated as a triggered-style consumer so the lord card pairs with tribe members.
    TRIGGER_PATTERNS.append((
        rf"(other )?{_tribe}s? (you control|you own).{{0,30}}(get|have|gain)",
        f"{_tribe} lord effect",
        f"tribal_{_t}_lord",
    ))

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

SYNERGY_CHUNK = 200   # producers per transaction — keeps each commit ~200×consumers rows
SYNERGY_LIMIT = int(os.environ.get("SYNERGY_LIMIT", "500000"))  # max edges per trigger_event

# PRODUCER_MAP: trigger_event → raw SQL WHERE fragment identifying PRODUCER cards.
# Producers are cards that GENERATE the event; consumers (from card_abilities) REACT to it.
# Values are raw SQL so we can use both oracle_text and type_line for precision.
PRODUCER_MAP: dict[str, str] = {
    # Cards that put NONTOKEN creatures onto the battlefield:
    #   reanimation (from graveyard), library cheating, blink
    "nontoken_etb": (
        # Graveyard reanimation
        "lower(oracle_text) LIKE '%return target%creature%battlefield%'"
        " OR lower(oracle_text) LIKE '%creature card from%graveyard%battlefield%'"
        " OR lower(oracle_text) LIKE '%creature card from a graveyard%battlefield%'"
        " OR lower(oracle_text) LIKE '%put target%creature%card%battlefield%'"
        # Library cheating
        " OR lower(oracle_text) LIKE '%search your library for a%creature%battlefield%'"
        " OR lower(oracle_text) LIKE '%look at the top%put%creature%battlefield%'"
        # Blink
        " OR lower(oracle_text) LIKE '%exile target%return%battlefield%'"
    ),
    # Cards that put ANY creatures onto the battlefield (tokens + reanimation + library)
    "creature_etb": (
        # Token creation
        "lower(oracle_text) LIKE '%create a%'"
        " OR lower(oracle_text) LIKE '%create two%'"
        " OR lower(oracle_text) LIKE '%create three%'"
        # Graveyard reanimation
        " OR lower(oracle_text) LIKE '%return target%creature%battlefield%'"
        " OR lower(oracle_text) LIKE '%creature card from%graveyard%battlefield%'"
        " OR lower(oracle_text) LIKE '%creature card from a graveyard%battlefield%'"
        # Library cheating
        " OR lower(oracle_text) LIKE '%search your library for a%creature%battlefield%'"
        " OR lower(oracle_text) LIKE '%look at the top%put%creature%battlefield%'"
        # Blink
        " OR lower(oracle_text) LIKE '%exile target%return%battlefield%'"
    ),
    # Cards that put artifacts onto the battlefield (artifact token makers, cheat-into-play)
    "artifact_etb": (
        "lower(oracle_text) LIKE '%create%treasure%'"
        " OR lower(oracle_text) LIKE '%create%food%'"
        " OR lower(oracle_text) LIKE '%create%clue%'"
        " OR lower(oracle_text) LIKE '%create%gold%'"
        " OR lower(oracle_text) LIKE '%put%artifact%battlefield%'"
        " OR lower(type_line) LIKE '%artifact%'"
    ),
    # Cards that PUT things onto the battlefield (token generators, reanimation, blink)
    "enters_battlefield": (
        "lower(oracle_text) LIKE '%create a%'"
        " OR lower(oracle_text) LIKE '%create two%'"
        " OR lower(oracle_text) LIKE '%create three%'"
        " OR lower(oracle_text) LIKE '%put onto the battlefield%'"
        " OR lower(oracle_text) LIKE '%return target%creature%battlefield%'"
        " OR lower(oracle_text) LIKE '%exile target%return%battlefield%'"
    ),
    # Cards that cause nontoken creatures to die (Aristocrats: sac outlets, kill spells, wipes)
    "nontoken_dies": (
        "lower(oracle_text) LIKE '%sacrifice a creature%'"
        " OR lower(oracle_text) LIKE '%sacrifice another%'"
        " OR lower(oracle_text) LIKE '%destroy target creature%'"
        " OR lower(oracle_text) LIKE '%destroy all creatures%'"
        " OR lower(oracle_text) LIKE '%each creature dies%'"
        " OR lower(oracle_text) LIKE '%creatures your opponents control%'"
    ),
    # Cards that cause any creature to die
    "dies": (
        "lower(oracle_text) LIKE '%sacrifice a creature%'"
        " OR lower(oracle_text) LIKE '%sacrifice another%'"
        " OR lower(oracle_text) LIKE '%destroy target creature%'"
        " OR lower(oracle_text) LIKE '%destroy all%'"
        " OR lower(oracle_text) LIKE '%deals damage%'"
    ),
    # Cards that enable or create attacking creatures (haste, tokens that attack)
    "attacks": (
        "lower(oracle_text) LIKE '% haste%'"
        " OR lower(oracle_text) LIKE '%must attack%'"
        " OR lower(oracle_text) LIKE '%attacks each combat%'"
        " OR lower(oracle_text) LIKE '%attacks each turn%'"
        " OR lower(oracle_text) LIKE '%with haste%'"
    ),
    # Cards that draw cards
    "draw": (
        "lower(oracle_text) LIKE '%draw a card%'"
        " OR lower(oracle_text) LIKE '%draw two cards%'"
        " OR lower(oracle_text) LIKE '%draw three cards%'"
        " OR lower(oracle_text) LIKE '%draw cards%'"
        " OR lower(oracle_text) LIKE '%draw x%'"
    ),
    # Instant and sorcery spells are the natural producers of "whenever you cast" triggers.
    # Also includes storm/cascade/flashback which generate extra casts.
    "spell_cast": (
        "lower(type_line) LIKE '%instant%'"
        " OR lower(type_line) LIKE '%sorcery%'"
        " OR lower(oracle_text) LIKE '%storm%'"
        " OR lower(oracle_text) LIKE '%cascade%'"
        " OR lower(oracle_text) LIKE '%flashback%'"
        " OR lower(oracle_text) LIKE '%cast another%'"
        " OR lower(oracle_text) LIKE '%cast an additional%'"
    ),
    # Cards with beginning-of-phase triggers or that accelerate phase effects
    "phase_begin": (
        "lower(oracle_text) LIKE '%at the beginning of%'"
        " OR lower(oracle_text) LIKE '%during your upkeep%'"
        " OR lower(oracle_text) LIKE '%each upkeep%'"
    ),
    # Cards that gain life or grant lifelink
    "lifegain": (
        "lower(oracle_text) LIKE '%you gain%life%'"
        " OR lower(oracle_text) LIKE '%gain life%'"
        " OR lower(oracle_text) LIKE '%gains life%'"
        " OR lower(oracle_text) LIKE '%lifelink%'"
        " OR lower(oracle_text) LIKE '%life equal to%'"
    ),
    # Cards that put lands into play (fetch effects, ramp spells)
    "landfall": (
        "lower(oracle_text) LIKE '%search your library for a%land%'"
        " OR lower(oracle_text) LIKE '%put a basic land%'"
        " OR lower(oracle_text) LIKE '%put a land%battlefield%'"
        " OR lower(oracle_text) LIKE '%play an additional land%'"
        " OR lower(oracle_text) LIKE '%land card onto the battlefield%'"
    ),
    # Cards that cause discarding (wheels, loot effects, discard outlets)
    "discard": (
        "lower(oracle_text) LIKE '%discard a card%'"
        " OR lower(oracle_text) LIKE '%discard your hand%'"
        " OR lower(oracle_text) LIKE '%each player discards%'"
        " OR lower(oracle_text) LIKE '%target player discards%'"
        " OR lower(oracle_text) LIKE '%discard two%'"
        " OR lower(oracle_text) LIKE '%draw a card, then discard%'"
    ),
    # Cards that specifically create tokens
    "token_creation": (
        "lower(oracle_text) LIKE '%create a%token%'"
        " OR lower(oracle_text) LIKE '%create two%'"
        " OR lower(oracle_text) LIKE '%create three%'"
        " OR lower(oracle_text) LIKE '%create x%'"
        " OR lower(oracle_text) LIKE '%put a%token%onto the battlefield%'"
    ),
    # Cards that add counters (proliferate, +1/+1 counter engines)
    "counter_added": (
        "lower(oracle_text) LIKE '%proliferate%'"
        " OR lower(oracle_text) LIKE '%put a +1/+1 counter%'"
        " OR lower(oracle_text) LIKE '%+1/+1 counter on each%'"
        " OR lower(oracle_text) LIKE '%put a counter on%'"
        " OR lower(oracle_text) LIKE '%double the number of counters%'"
    ),
    # Cards with evasion or power that deal combat damage
    "combat_damage": (
        "lower(oracle_text) LIKE '%can''t be blocked%'"
        " OR lower(oracle_text) LIKE '%double strike%'"
        " OR lower(oracle_text) LIKE '%trample%'"
        " OR lower(oracle_text) LIKE '%menace%'"
        " OR lower(oracle_text) LIKE '%deals combat damage%'"
    ),
    # Sacrifice outlets (cards that let you sacrifice as cost or effect)
    "sacrifice": (
        "lower(oracle_text) LIKE '%sacrifice a creature%'"
        " OR lower(oracle_text) LIKE '%sacrifice another%'"
        " OR lower(oracle_text) LIKE '%sacrifice a permanent%'"
        " OR lower(oracle_text) LIKE '%sacrifice target%'"
        " OR lower(oracle_text) LIKE '%sacrifice:%'"
    ),
}

# Tribal producers: cards of each creature type generate both cast and ETB tribal events.
# Add entries for both trigger sub-types so the consumer query finds them.
for _tribe in TRIBES:
    _t = _tribe.lower()
    _where = f"lower(type_line) LIKE '%{_t}%'"
    PRODUCER_MAP[f"tribal_{_t}_cast"] = _where
    PRODUCER_MAP[f"tribal_{_t}_etb"] = _where
    PRODUCER_MAP[f"tribal_{_t}_lord"] = _where  # lord consumers pair with tribe members


async def compute_synergy() -> None:
    """Build synergy edges in small chunked transactions.

    Fetches producer card IDs in Python, then drives INSERT...SELECT statements
    SYNERGY_CHUNK producers at a time so no single transaction materialises more
    than ~200 × consumers rows.  Progress is checkpointed after every chunk so
    a restart resumes without duplicates (ON CONFLICT DO NOTHING).
    """
    log.info("Computing ability-trigger synergy edges…")

    for trigger_event, producer_where in PRODUCER_MAP.items():
        # Fetch just the IDs of producer cards — small result set
        async with Session() as db:
            rows = (await db.execute(text(f"""
                SELECT id FROM cards WHERE {producer_where}
            """))).fetchall()
        producer_ids = [str(r[0]) for r in rows]

        if not producer_ids:
            log.info("  %s → no producers found, skipping", trigger_event)
            continue

        total_inserted = 0
        n_chunks = (len(producer_ids) + SYNERGY_CHUNK - 1) // SYNERGY_CHUNK
        log.info("  %s: %d producers in %d chunks…", trigger_event, len(producer_ids), n_chunks)

        for chunk_idx in range(0, len(producer_ids), SYNERGY_CHUNK):
            if total_inserted >= SYNERGY_LIMIT:
                log.info("  %s: SYNERGY_LIMIT=%d reached, stopping early",
                         trigger_event, SYNERGY_LIMIT)
                break

            chunk = producer_ids[chunk_idx : chunk_idx + SYNERGY_CHUNK]
            id_list = "'" + "','".join(chunk) + "'"

            async with Session() as db:
                result = await db.execute(text(f"""
                    INSERT INTO synergy_edges (card_a, card_b, score_type, score, metadata)
                    SELECT
                        c.id::uuid,
                        ca.card_id,
                        'ability_trigger',
                        1.0,
                        '{{"trigger_event": "{trigger_event}"}}'::jsonb
                    FROM (SELECT unnest(ARRAY[{id_list}]::uuid[]) AS id) c
                    CROSS JOIN (
                        SELECT card_id FROM card_abilities
                        WHERE trigger_event = '{trigger_event}'
                    ) ca
                    WHERE c.id != ca.card_id
                    ON CONFLICT (card_a, card_b, score_type) DO NOTHING
                """))
                await db.commit()
            total_inserted += result.rowcount

            if (chunk_idx // SYNERGY_CHUNK) % 10 == 0:
                log.info("    chunk %d/%d — %d edges so far",
                         chunk_idx // SYNERGY_CHUNK + 1, n_chunks, total_inserted)

        log.info("  %s → %d edges total", trigger_event, total_inserted)


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
