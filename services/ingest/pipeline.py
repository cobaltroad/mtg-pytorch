"""
MTG ingest pipeline.

Two-step workflow
-----------------
download   – Fetch card data (MTGJSON → cards table) + import combos (Commander Spellbook).
             Re-run when new sets release or combo data changes.
process    – Embed cards, tag abilities, compute synergy edges, export training artifact.
             Re-run after download or after model/pattern changes.

Run both:           python pipeline.py
Run download only:  python pipeline.py --stage download
Run process only:   python pipeline.py --stage process

Individual sub-stages (rarely needed):
  embed_cards, tag_abilities [--rescan], compute_synergy,
  compute_commander_value_synergy, compute_tribal_typeline_synergy,
  export_dataset, export_dataset_compositional

Data sources
------------
Primary:  MTGJSON bulk downloads (https://mtgjson.com/downloads/)
          No rate limits; full machine-readable dataset.
Fallback: Scryfall oracle_cards bulk JSON — only used if MTGJSON unavailable,
          because Scryfall enforces strict rate limits on their API.
Combos:   Commander Spellbook API — fetched during download step.
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
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "sentence-transformers/all-mpnet-base-v2")
CACHE_DIR = Path(os.environ.get("MTGJSON_CACHE_DIR", "/data"))
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

from land_tags import annotate_land_oracle  # noqa: E402


def _card_text(row) -> str:
    parts = [row[1]]  # name
    if row[4]:         # type_line
        parts.append(row[4])
    oracle = row[5] or ""
    if oracle:
        # Augment Land oracle text with structured mana-quality tags so the
        # model learns that Verdant Catacombs and Woodland Cemetery cluster
        # together rather than being separated by superficial text differences.
        if row[4] and "Land" in row[4]:
            oracle = annotate_land_oracle(oracle)
        parts.append(oracle)
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

from synergy import (  # noqa: E402
    TRIGGER_PATTERNS, PRODUCER_MAP,
    TRIBES, ALL_TYPES_SQL,
    ROLE_PATTERNS, LAND_ROLE_PATTERNS, is_land_card,
    COMMANDER_VALUE_TRIGGER_PATTERNS,
    COMMANDER_VALUE_PRODUCER_MAP,
    COMMANDER_VALUE_EDGE_SCORES,
    XMAGE_PRODUCER_MAP,
    SPELLCAST_TRIGGER_PRODUCER_MAP,
)

KEYWORD_RE = re.compile(
    r"\b(flying|trample|haste|vigilance|deathtouch|lifelink|reach|hexproof|"
    r"indestructible|flash|first strike|double strike|menace|prowess|"
    r"ward|protection|shroud|defender|annihilator|cascade|convoke|"
    r"delve|exploit|fabricate|flashback|kicker|madness|miracle|"
    r"morph|overload|persist|proliferate|rebound|replicate|retrace|"
    r"scry|storm|suspend|threshold|undying|unearth|wither)\b",
    re.IGNORECASE,
)


def _tag_roles(oracle_text: str, type_line: str) -> list[dict]:
    """Return a list of role-tag dicts for a single card.

    Applies :data:`ROLE_PATTERNS` against *oracle_text* and, when the card is
    a Land, also applies :data:`LAND_ROLE_PATTERNS`.  Each matching role is
    emitted at most once per card (duplicates within a single card are dropped).

    Args:
        oracle_text: The card's oracle text (may contain newlines for MDFCs).
        type_line:   The card's type line (e.g. "Legendary Creature — Zombie").

    Returns:
        A list of ``card_abilities``-shaped dicts with ``ability_type='role'``.
    """
    # Deduplication key is (role_name, effect_class) so a card can receive
    # multiple rows for the same flat role if it has distinct effect subtypes
    # (e.g. a card that both destroys creatures and exiles enchantments).
    seen_role_classes: set[tuple[str, str]] = set()
    rows: list[dict] = []

    patterns = list(ROLE_PATTERNS)
    if is_land_card(type_line):
        patterns = patterns + list(LAND_ROLE_PATTERNS)

    for pattern, role_name, effect_class in patterns:
        if (role_name, effect_class) in seen_role_classes:
            continue
        m = re.search(pattern, oracle_text, re.IGNORECASE)
        if m:
            seen_role_classes.add((role_name, effect_class))
            rows.append({
                "ability_type": "role",
                "ability_name": role_name,
                "trigger_event": None,
                "effect_class": effect_class,
                "raw_text": m.group(0)[:200],
            })

    return rows


async def tag_abilities(rescan: bool = False) -> None:
    log.info("Tagging abilities%s…", " (full rescan)" if rescan else "")

    # Snapshot which trigger_events already have consumer rows BEFORE Pass 1 so
    # the gap detector doesn't miss events that get their first rows from newly
    # added cards in this very run.
    # With --rescan, treat the existing set as empty so every trigger event is
    # re-applied across all cards (useful after a pattern regex is improved).
    if rescan:
        existing_events: set[str] = set()
    else:
        async with Session() as db:
            existing_events_result = await db.execute(text("""
                SELECT DISTINCT trigger_event FROM card_abilities
                WHERE trigger_event IS NOT NULL
            """))
            existing_events = {row[0] for row in existing_events_result.fetchall()}

    # ── Pass 1: keyword / triggered / activated abilities ─────────────────────
    # Processes cards that have no ability rows yet (fresh cards).
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

    log.info("Tagging %d cards (keyword/triggered)…", len(rows))
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
                        "ability_type": "triggered" if "trigger" in name else ("static" if "lord" in name or "anthem" in name else "activated"),
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

    # ── Pass 1b: gap detection — apply new trigger patterns to existing cards ──
    # When a new trigger_event is added to TRIGGER_PATTERNS after the initial
    # tag_abilities run, existing cards are skipped by Pass 1 (they already have
    # ability rows).  This pass detects trigger_events with 0 consumer rows and
    # re-processes ALL cards for just those new events.
    #
    # We use the pre-Pass-1 snapshot so events that got their first rows from
    # newly ingested cards during Pass 1 are still detected as gaps (they won't
    # have rows on any *existing* cards).
    new_events = [
        (pattern, name, event)
        for pattern, name, event in TRIGGER_PATTERNS
        if event and event not in existing_events
    ]

    if new_events:
        log.info(
            "%s: %d trigger event(s) — backfilling across all cards: %s",
            "Full rescan" if rescan else "Gap detection",
            len(new_events), [e for _, _, e in new_events],
        )
        async with Session() as db:
            all_cards_result = await db.execute(text("""
                SELECT c.id, c.oracle_text FROM cards c WHERE c.oracle_text IS NOT NULL
            """))
            all_cards = all_cards_result.fetchall()

        log.info("  Scanning %d cards for %d new event(s)…", len(all_cards), len(new_events))
        backfill_count = 0
        async with Session() as db:
            for card_id, oracle_text in tqdm(all_cards):
                inserts = []
                for pattern, name, event in new_events:
                    m = re.search(pattern, oracle_text, re.IGNORECASE)
                    if m:
                        inserts.append({
                            "card_id": str(card_id),
                            "ability_type": "triggered" if "trigger" in name else ("static" if "lord" in name or "anthem" in name else "activated"),
                            "ability_name": name,
                            "trigger_event": event,
                            "effect_class": None,
                            "raw_text": m.group(0)[:200],
                        })
                if inserts:
                    await db.execute(text("""
                        INSERT INTO card_abilities
                            (card_id, ability_type, ability_name, trigger_event, effect_class, raw_text)
                        VALUES
                            (:card_id, :ability_type, :ability_name, :trigger_event, :effect_class, :raw_text)
                        ON CONFLICT (card_id, ability_type, ability_name, COALESCE(effect_class, '')) DO NOTHING
                    """), inserts)
                    backfill_count += len(inserts)

            await db.commit()
        log.info("  Gap detection complete: %d rows inserted", backfill_count)
    else:
        log.info("Gap detection: all trigger events already have consumer rows — nothing to backfill (use --rescan to force)")

    # ── Pass 2: functional role tags ──────────────────────────────────────────
    # Incremental: processes cards that have no 'role' ability rows yet.
    # Rescan: deletes all ingest-written role rows (effect_class IS NULL —
    # the old pre-structured-effect_class format) then re-processes every card,
    # writing the new structured effect_class values.  API-written role rows
    # (non-null effect_class in the old API naming convention) are left in place;
    # they coexist harmlessly in card_abilities and are gradually superseded as
    # backfill_roles re-runs against the updated tag_card_roles() in the API.
    if rescan:
        async with Session() as db:
            deleted = await db.execute(text("""
                DELETE FROM card_abilities
                WHERE ability_type = 'role' AND effect_class IS NULL
            """))
            await db.commit()
        log.info("Rescan: deleted %d stale role rows (effect_class IS NULL)", deleted.rowcount)

    async with Session() as db:
        if rescan:
            result = await db.execute(text("""
                SELECT c.id, c.oracle_text, c.type_line
                FROM cards c
                WHERE c.oracle_text IS NOT NULL
            """))
        else:
            result = await db.execute(text("""
                SELECT c.id, c.oracle_text, c.type_line
                FROM cards c
                WHERE NOT EXISTS (
                    SELECT 1 FROM card_abilities a
                    WHERE a.card_id = c.id AND a.ability_type = 'role'
                )
                AND c.oracle_text IS NOT NULL
            """))
        role_rows = result.fetchall()

    log.info("Tagging roles for %d cards…", len(role_rows))
    async with Session() as db:
        for row in tqdm(role_rows):
            card_id, oracle_text, type_line = row[0], row[1] or "", row[2] or ""
            role_inserts = [
                {**rd, "card_id": str(card_id)}
                for rd in _tag_roles(oracle_text, type_line)
            ]
            if role_inserts:
                await db.execute(text("""
                    INSERT INTO card_abilities
                        (card_id, ability_type, ability_name, trigger_event, effect_class, raw_text)
                    VALUES
                        (:card_id, :ability_type, :ability_name, :trigger_event, :effect_class, :raw_text)
                    ON CONFLICT (card_id, ability_type, ability_name, COALESCE(effect_class, '')) DO NOTHING
                """), role_inserts)

        await db.commit()


# ── Stage 5: Synergy edges ────────────────────────────────────────────────────

SYNERGY_CHUNK = 200   # producers per transaction — keeps each commit ~200×consumers rows
SYNERGY_LIMIT = int(os.environ.get("SYNERGY_LIMIT", "500000"))  # max edges per trigger_event





TRIBAL_MEMBER_LIMIT = int(os.environ.get("TRIBAL_MEMBER_LIMIT", "50_000"))
"""Max intra-tribal member→member edges per tribe (commander→member edges are uncapped)."""

# Maximum commander_value edges per trigger_event.
COMMANDER_VALUE_LIMIT = int(os.environ.get("COMMANDER_VALUE_LIMIT", "500_000"))


async def compute_commander_value_synergy() -> None:
    """Build synergy edges between low-MV commanders and commander-value cards.

    These edges capture the synergy between:

    * **Low-MV commanders** (CMC ≤ 2 legendary creatures / planeswalkers) — the
      *producers* — which are frequently in play and easy to recast, maximising
      the value extracted from commander-conditional support cards.

    * **Commander-value cards** — the *consumers* — whose oracle text grants a
      meaningful benefit specifically when you control your commander:

      - ``commander_free_cast`` (score 1.0): spells that may be cast for free
        while a commander is in play (Deflecting Swat, Fierce Guardianship,
        Flawless Maneuver, Deadly Rollick, …).
      - ``commander_in_play_payoff`` (score 0.8): permanents / spells that gain
        abilities, produce bonus mana, or otherwise improve while a commander is
        present (Loyal Apprentice, Jeska's Will, Loran's Escape, …).
      - ``commander_mana_value`` (score 0.6): cards whose mana output references
        a legendary creature or planeswalker you control (Mox Amber, Selvala
        Heart of the Wilds, …).  For this event the producer pool is widened to
        all legendary creatures/planeswalkers (no CMC cap) because Mox Amber
        works with any legend, not just cheap ones.

    All edges are written with ``score_type = 'commander_value'`` so they are
    kept separate from ``ability_trigger`` edges and can be queried or weighted
    independently during training and deck generation.

    The direction of each edge is:
        card_a = producer (low-MV commander)
        card_b = consumer (commander-value payoff card)

    Color-identity filtering is intentionally skipped here because the
    commander-value cards (e.g. Deflecting Swat) typically belong to a single
    color and would naturally end up in a legal deck — color legality is
    enforced at deck-generation time.
    """
    log.info("Computing commander-value synergy edges…")

    for trigger_event, producer_where in COMMANDER_VALUE_PRODUCER_MAP.items():
        score = COMMANDER_VALUE_EDGE_SCORES.get(trigger_event, 0.6)

        # Fetch producer card IDs (low-MV legendary creatures / planeswalkers)
        async with Session() as db:
            prod_rows = (await db.execute(text(f"""
                SELECT id FROM cards WHERE {producer_where}
            """))).fetchall()
        producer_ids = [str(r[0]) for r in prod_rows]

        if not producer_ids:
            log.info("  commander_value/%s → no producers, skipping", trigger_event)
            continue

        # Fetch consumer card IDs (tagged with this trigger_event in card_abilities)
        async with Session() as db:
            cons_rows = (await db.execute(text(f"""
                SELECT DISTINCT ca.card_id
                FROM card_abilities ca
                WHERE ca.trigger_event = '{trigger_event}'
            """))).fetchall()
        consumer_ids = [str(r[0]) for r in cons_rows]

        if not consumer_ids:
            log.info("  commander_value/%s → no consumers tagged, skipping", trigger_event)
            continue

        total_inserted = 0
        n_chunks = (len(producer_ids) + SYNERGY_CHUNK - 1) // SYNERGY_CHUNK
        log.info(
            "  commander_value/%s: %d producers × %d consumers in %d chunks (score=%.1f)…",
            trigger_event, len(producer_ids), len(consumer_ids), n_chunks, score,
        )

        consumer_list = "'" + "','".join(consumer_ids) + "'"

        for chunk_idx in range(0, len(producer_ids), SYNERGY_CHUNK):
            if total_inserted >= COMMANDER_VALUE_LIMIT:
                log.info(
                    "  commander_value/%s: COMMANDER_VALUE_LIMIT=%d reached, stopping",
                    trigger_event, COMMANDER_VALUE_LIMIT,
                )
                break

            chunk = producer_ids[chunk_idx : chunk_idx + SYNERGY_CHUNK]
            id_list = "'" + "','".join(chunk) + "'"

            async with Session() as db:
                result = await db.execute(text(f"""
                    INSERT INTO synergy_edges
                        (card_a, card_b, score_type, score, metadata)
                    SELECT
                        p.id::uuid,
                        c.id::uuid,
                        'commander_value',
                        {score},
                        '{{"trigger_event": "{trigger_event}"}}'::jsonb
                    FROM (SELECT unnest(ARRAY[{id_list}]::uuid[]) AS id) p
                    CROSS JOIN (SELECT unnest(ARRAY[{consumer_list}]::uuid[]) AS id) c
                    WHERE p.id != c.id
                    ON CONFLICT (card_a, card_b, score_type) DO NOTHING
                """))
                await db.commit()
            total_inserted += result.rowcount

        log.info("  commander_value/%s → %d edges", trigger_event, total_inserted)

    log.info("Commander-value synergy complete")


async def compute_tribal_typeline_synergy() -> None:
    """Build synergy edges between tribal commanders and tribe members.

    Two kinds of edges are generated for each tribe in TRIBES:

    1. Commander → member  (uncapped)
       Legendary creature cards whose oracle text mentions the tribe name
       (e.g. "Zombie", "Elf") paired with every card of that tribe.
       Requiring the tribe to appear in oracle text prevents false positives
       from commanders that merely happen to share a creature type (e.g. a
       Legendary Human with no Human-matters text should not get Human edges).

    2. Member → member  (capped at TRIBAL_MEMBER_LIMIT per tribe)
       All tribe members paired with each other, so intra-tribal co-occurrence
       is reflected in the embedding space.

    Changelings ('Changeling' = ANY(keywords)) are included in every tribe's
    member pool because they are every creature type simultaneously — e.g.
    Mothdust Changeling and Graveshifter count as Zombies for Wilhelt edges.

    Both use score_type='ability_trigger' so Phase 2 training picks them up
    without any changes to train.py.
    """
    log.info("Computing tribal type_line synergy edges…")

    for tribe in TRIBES:
        t = tribe.lower()

        async with Session() as db:
            # Changelings ('Changeling' = ANY(keywords)) are every creature type, so
            # they belong to every tribe's member pool regardless of type_line.
            all_members = (await db.execute(text(f"""
                SELECT id::text FROM cards
                WHERE (
                    (lower(type_line) LIKE '%{t}%' AND lower(type_line) LIKE '%creature%')
                    OR {ALL_TYPES_SQL}
                )
            """))).fetchall()
            # Only commanders whose oracle text explicitly mentions the tribe name
            # qualify for commander→member edges.  Matching solely on type_line
            # would pair every Legendary Human with all Humans, etc., even when
            # the card has no Human-matters text — a major source of false positives.
            commanders = (await db.execute(text(f"""
                SELECT id::text FROM cards
                WHERE lower(type_line) LIKE '%creature%'
                  AND lower(type_line) LIKE '%legendary%'
                  AND lower(oracle_text) LIKE '%{t}%'
            """))).fetchall()

        member_ids = [r[0] for r in all_members]
        cmd_ids    = [r[0] for r in commanders]

        if not member_ids:
            log.info("  %s: no members found, skipping", tribe)
            continue

        log.info("  %s: %d members, %d legendary commanders", tribe, len(member_ids), len(cmd_ids))

        # Check how many edges already exist for this tribe so the "0 new edges"
        # log from ON CONFLICT DO NOTHING isn't misread as a failure.
        async with Session() as db:
            existing_tribal = (await db.execute(text(f"""
                SELECT count(*) FROM synergy_edges
                WHERE metadata->>'trigger_event' = 'tribal_{t}_typeline'
            """))).scalar()
        if existing_tribal:
            log.info("  %s: %d existing typeline edges (new inserts skipped via ON CONFLICT)",
                     tribe, existing_tribal)

        # ── 1. Commander → all tribe members (uncapped) ─────────────────────
        cmd_inserted = 0
        for chunk_start in range(0, len(cmd_ids), SYNERGY_CHUNK):
            chunk = cmd_ids[chunk_start : chunk_start + SYNERGY_CHUNK]
            id_list    = "'" + "','".join(chunk) + "'"
            member_list = "'" + "','".join(member_ids) + "'"
            async with Session() as db:
                result = await db.execute(text(f"""
                    INSERT INTO synergy_edges (card_a, card_b, score_type, score, metadata)
                    SELECT
                        c.id::uuid,
                        m.id::uuid,
                        'ability_trigger',
                        1.0,
                        '{{"trigger_event": "tribal_{t}_typeline", "role": "commander_member"}}'::jsonb
                    FROM (SELECT unnest(ARRAY[{id_list}]::uuid[]) AS id) c
                    CROSS JOIN (SELECT unnest(ARRAY[{member_list}]::uuid[]) AS id) m
                    WHERE c.id != m.id
                    ON CONFLICT (card_a, card_b, score_type) DO NOTHING
                """))
                await db.commit()
            cmd_inserted += result.rowcount
        log.info("    commander→member: %d new edges (existing skipped via ON CONFLICT)", cmd_inserted)

        # ── 2. Member → member (capped) ──────────────────────────────────────
        member_inserted = 0
        for chunk_start in range(0, len(member_ids), SYNERGY_CHUNK):
            if member_inserted >= TRIBAL_MEMBER_LIMIT:
                log.info("    TRIBAL_MEMBER_LIMIT=%d reached for %s, stopping",
                         TRIBAL_MEMBER_LIMIT, tribe)
                break
            chunk = member_ids[chunk_start : chunk_start + SYNERGY_CHUNK]
            id_list     = "'" + "','".join(chunk) + "'"
            member_list = "'" + "','".join(member_ids) + "'"
            async with Session() as db:
                result = await db.execute(text(f"""
                    INSERT INTO synergy_edges (card_a, card_b, score_type, score, metadata)
                    SELECT
                        c.id::uuid,
                        m.id::uuid,
                        'ability_trigger',
                        1.0,
                        '{{"trigger_event": "tribal_{t}_typeline", "role": "member_member"}}'::jsonb
                    FROM (SELECT unnest(ARRAY[{id_list}]::uuid[]) AS id) c
                    CROSS JOIN (SELECT unnest(ARRAY[{member_list}]::uuid[]) AS id) m
                    WHERE c.id != m.id
                    ON CONFLICT (card_a, card_b, score_type) DO NOTHING
                """))
                await db.commit()
            member_inserted += result.rowcount
        log.info("    member→member: %d new edges (existing skipped via ON CONFLICT)", member_inserted)

    log.info("Tribal type_line synergy complete")


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

        # Count consumers before running chunks — 0 consumers means the
        # trigger_event pattern produced no card_abilities rows (tag_abilities
        # gap) and all chunks would silently produce 0 inserts.
        async with Session() as db:
            consumer_count = (await db.execute(text(f"""
                SELECT COUNT(*) FROM card_abilities
                WHERE trigger_event = '{trigger_event}'
            """))).scalar()
        if not consumer_count:
            log.warning(
                "  %s → 0 consumer rows in card_abilities — skipping "
                "(run tag_abilities to backfill this trigger event)",
                trigger_event,
            )
            continue

        total_inserted = 0
        n_chunks = (len(producer_ids) + SYNERGY_CHUNK - 1) // SYNERGY_CHUNK
        log.info("  %s: %d producers × %d consumers in %d chunks…",
                 trigger_event, len(producer_ids), consumer_count, n_chunks)

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
                    JOIN cards pc ON pc.id = c.id
                    CROSS JOIN (
                        SELECT ca.card_id, cc.color_identity AS consumer_ci
                        FROM card_abilities ca
                        JOIN cards cc ON cc.id = ca.card_id
                        WHERE ca.trigger_event = '{trigger_event}'
                    ) ca
                    WHERE c.id != ca.card_id
                      AND (
                          pc.color_identity = '{{}}'
                          OR ca.consumer_ci = '{{}}'
                          OR pc.color_identity && ca.consumer_ci
                      )
                    ON CONFLICT (card_a, card_b, score_type) DO NOTHING
                """))
                await db.commit()
            total_inserted += result.rowcount

            if (chunk_idx // SYNERGY_CHUNK) % 10 == 0:
                log.info("    chunk %d/%d — %d edges so far",
                         chunk_idx // SYNERGY_CHUNK + 1, n_chunks, total_inserted)

        log.info("  %s → %d new edges (existing skipped via ON CONFLICT)", trigger_event, total_inserted)


async def _xmage_insert_edges(
    ability_class: str,
    producer_where: str,
    trigger_event_filter: str | None = None,
) -> int:
    """Insert xmage_ability_trigger synergy edges for one (ability_class, trigger_event) bucket.

    Returns the number of new rows inserted.

    ``trigger_event_filter`` — when set, only consumers whose ``trigger_event``
    matches this value are included.  Used to sub-group
    ``SpellCastControllerTriggeredAbility`` by spell type.
    """
    async with Session() as db:
        rows = (await db.execute(text(f"""
            SELECT id FROM cards WHERE {producer_where}
        """))).fetchall()
    producer_ids = [str(r[0]) for r in rows]

    if not producer_ids:
        return 0

    # Build the consumer WHERE clause
    consumer_filter = "ca.ability_name = :cls AND ca.source = 'xmage'"
    params: dict = {"cls": ability_class}
    if trigger_event_filter is not None:
        consumer_filter += " AND COALESCE(ca.trigger_event, 'spell_cast') = :te"
        params["te"] = trigger_event_filter

    async with Session() as db:
        consumer_count = (await db.execute(
            text(f"SELECT COUNT(*) FROM card_abilities ca WHERE {consumer_filter}"),
            params,
        )).scalar()

    if not consumer_count:
        return 0

    label = ability_class if trigger_event_filter is None else f"{ability_class}[{trigger_event_filter}]"
    n_chunks = (len(producer_ids) + SYNERGY_CHUNK - 1) // SYNERGY_CHUNK
    log.info("  %s: %d producers × %d consumers in %d chunks…",
             label, len(producer_ids), consumer_count, n_chunks)

    total_inserted = 0
    metadata = jsonb_build = (
        f"jsonb_build_object('ability_class', :cls, 'trigger_event', '{trigger_event_filter}')"
        if trigger_event_filter is not None
        else "jsonb_build_object('ability_class', :cls)"
    )

    for chunk_idx in range(0, len(producer_ids), SYNERGY_CHUNK):
        if total_inserted >= SYNERGY_LIMIT:
            log.info("  %s: SYNERGY_LIMIT=%d reached, stopping early", label, SYNERGY_LIMIT)
            break

        chunk = producer_ids[chunk_idx : chunk_idx + SYNERGY_CHUNK]
        id_list = "'" + "','".join(chunk) + "'"

        async with Session() as db:
            result = await db.execute(text(f"""
                INSERT INTO synergy_edges (card_a, card_b, score_type, score, metadata)
                SELECT
                    c.id::uuid,
                    ca.card_id,
                    'xmage_ability_trigger',
                    1.0,
                    {metadata}
                FROM (SELECT unnest(ARRAY[{id_list}]::uuid[]) AS id) c
                JOIN cards pc ON pc.id = c.id
                CROSS JOIN (
                    SELECT ca.card_id, cc.color_identity AS consumer_ci
                    FROM card_abilities ca
                    JOIN cards cc ON cc.id = ca.card_id
                    WHERE {consumer_filter}
                ) ca
                WHERE c.id != ca.card_id
                  AND (
                      pc.color_identity = '{{}}'
                      OR ca.consumer_ci = '{{}}'
                      OR pc.color_identity && ca.consumer_ci
                  )
                ON CONFLICT (card_a, card_b, score_type) DO NOTHING
            """), params)
            await db.commit()
        total_inserted += result.rowcount

        if (chunk_idx // SYNERGY_CHUNK) % 10 == 0:
            log.info("    chunk %d/%d — %d edges so far",
                     chunk_idx // SYNERGY_CHUNK + 1, n_chunks, total_inserted)

    log.info("  %s → %d new edges", label, total_inserted)
    return total_inserted


async def compute_synergy_xmage() -> None:
    """Build XMage-class synergy edges for the compositional training path.

    Reads ``card_abilities`` rows where ``source='xmage'``, groups by
    ``ability_name`` (the raw XMage class name), then cross-joins each consumer
    group with the producer cards defined in ``XMAGE_PRODUCER_MAP``.

    ``SpellCastControllerTriggeredAbility`` is handled specially: consumers are
    sub-grouped by their refined ``trigger_event`` (set by the body-scan in
    ``xmage_parse.py``) and each sub-bucket uses the type-specific producer SQL
    from ``SPELLCAST_TRIGGER_PRODUCER_MAP``.  This prevents Sythis (enchantment
    cast) from being paired with Guttersnipe (instant/sorcery) or Beast Whisperer
    (creature cast) as positive synergy pairs.

    Edges are written with ``score_type='xmage_ability_trigger'`` so they are
    kept entirely separate from the pattern-based ``ability_trigger`` edges used
    by the co-occurrence training path.
    """
    log.info("Computing XMage-class synergy edges (compositional path)…")

    for ability_class, default_producer_where in XMAGE_PRODUCER_MAP.items():
        if ability_class == "SpellCastControllerTriggeredAbility":
            # Sub-group by trigger_event so each spell-type bucket gets the
            # correct producers (enchantments for Sythis, creatures for Beast
            # Whisperer, instants/sorceries for Guttersnipe, etc.)
            async with Session() as db:
                te_rows = (await db.execute(text("""
                    SELECT COALESCE(trigger_event, 'spell_cast') AS te, COUNT(*) AS cnt
                    FROM card_abilities
                    WHERE ability_name = :cls AND source = 'xmage'
                    GROUP BY COALESCE(trigger_event, 'spell_cast')
                """), {"cls": ability_class})).fetchall()

            if not te_rows:
                log.info("  %s → 0 xmage consumers, skipping", ability_class)
                continue

            for trigger_event, cnt in te_rows:
                producer_where = SPELLCAST_TRIGGER_PRODUCER_MAP.get(
                    trigger_event, default_producer_where
                )
                await _xmage_insert_edges(ability_class, producer_where, trigger_event)
            continue

        # All other ability classes: one producer bucket, no trigger_event filter
        inserted = await _xmage_insert_edges(ability_class, default_producer_where)
        if inserted == 0:
            async with Session() as db:
                consumer_count = (await db.execute(text("""
                    SELECT COUNT(*) FROM card_abilities
                    WHERE ability_name = :cls AND source = 'xmage'
                """), {"cls": ability_class})).scalar()
            if not consumer_count:
                log.info("  %s → 0 xmage consumers, skipping", ability_class)
            else:
                log.info("  %s → no producers found, skipping", ability_class)


# ── Entry point ───────────────────────────────────────────────────────────────

async def import_spellbook_stage() -> None:
    """Import Commander Spellbook combos into combo_packages / combo_package_cards."""
    import import_spellbook
    await import_spellbook.main()


def export_dataset_stage() -> None:
    """Serialize the full training artifact to /data/mtg_dataset.pt."""
    import export_dataset
    export_dataset.main()


def export_dataset_compositional_stage() -> None:
    """Serialize the compositional training artifact to /data/mtg_dataset_compositional.pt."""
    import export_dataset_compositional
    export_dataset_compositional.main()


async def composition_profile_stage() -> None:
    """Rebuild /data/deck_composition_profile.json from the imported deck pool.

    Always regenerates — call after importing new decklists or after a full
    process run so the API's structural targets stay current.
    """
    import deck_composition_profile as dcp
    log.info("Regenerating deck composition profile → %s", dcp.OUTPUT_FILE)
    await dcp.main()


async def download():
    """Download step: fetch card data + combos and load into DB.

    Run this first (or whenever MTGJSON / Commander Spellbook has new data).
    Does not require embeddings or synergy edges to be present.
    """
    path, source = await fetch_cards()
    await load_cards(path, source)
    await import_spellbook_stage()


async def process():
    """Process step: embed, tag, compute synergy, and export the artifact.

    Requires the download step to have been run first.
    """
    await embed_cards()
    await tag_abilities()
    await compute_synergy()
    await compute_commander_value_synergy()
    await compute_tribal_typeline_synergy()
    export_dataset_stage()
    await composition_profile_stage()


async def run_all():
    await download()
    await process()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stage",
        choices=[
            # Grouped stages
            "download", "process",
            # Individual sub-stages
            "embed_cards", "tag_abilities", "tag_abilities_xmage",
            "compute_synergy", "compute_synergy_xmage",
            "compute_commander_value_synergy",
            "compute_tribal_typeline_synergy",
            "export_dataset", "export_dataset_compositional", "composition_profile",
        ],
        default=None,
        help=(
            "download: fetch MTGJSON + load cards + import combos. "
            "process: embed + tag + synergy + export + composition_profile. "
            "tag_abilities_xmage: supplement card_abilities from XMage source tree "
            "(requires XMAGE_DIR env var or --xmage-dir; mount mage/ read-only). "
            "composition_profile: rebuild deck_composition_profile.json "
            "(run after importing new decklists). "
            "Omit to run both."
        ),
    )
    parser.add_argument(
        "--rescan",
        action="store_true",
        help=(
            "tag_abilities only: re-apply every trigger pattern to every card, "
            "not just those with 0 existing rows.  Use after improving a pattern regex."
        ),
    )
    args = parser.parse_args()

    if args.stage == "download":
        asyncio.run(download())
    elif args.stage == "process":
        asyncio.run(process())
    elif args.stage == "embed_cards":
        asyncio.run(embed_cards())
    elif args.stage == "tag_abilities":
        asyncio.run(tag_abilities(rescan=args.rescan))
    elif args.stage == "tag_abilities_xmage":
        from xmage_parse import tag_abilities_xmage as _xmage_tag
        import os as _os
        from pathlib import Path as _Path
        asyncio.run(_xmage_tag(_Path(_os.environ.get("XMAGE_DIR", "/mage"))))
    elif args.stage == "compute_synergy":
        asyncio.run(compute_synergy())
    elif args.stage == "compute_synergy_xmage":
        asyncio.run(compute_synergy_xmage())
    elif args.stage == "compute_commander_value_synergy":
        asyncio.run(compute_commander_value_synergy())
    elif args.stage == "compute_tribal_typeline_synergy":
        asyncio.run(compute_tribal_typeline_synergy())
    elif args.stage == "export_dataset":
        export_dataset_stage()
    elif args.stage == "export_dataset_compositional":
        export_dataset_compositional_stage()
    elif args.stage == "composition_profile":
        asyncio.run(composition_profile_stage())
    else:
        asyncio.run(run_all())
