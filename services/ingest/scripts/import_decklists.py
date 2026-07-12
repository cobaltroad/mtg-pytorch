"""
Import Commander decklists from the cardtrak ml_decklists export into the
mtg-pytorch decks table.

Input:  /data/ml_decklists.json  (exported from cardtrak_production)
Output: rows inserted into the decks table (commander_id, card_ids[])

Matching strategy:
  Commander is identified by deck_name (is_commander flag is not reliable).
  Cards are matched by lower-cased name against the cards table.
  Cards not found in our DB (new printings, tokens, etc.) are skipped.
  Decks where the commander can't be resolved are skipped entirely.

deck_list comes in two formats from cardtrak:
  - list of card dicts (most decks)
  - dict with 'cards' key (some decks)

Usage (from repo root):
    # 1. Export from cardtrak DB on the host:
    docker exec cardtrak_db psql -U cardtrak -d cardtrak_production \\
        -t -c "SELECT json_agg(row_to_json(d)) FROM ml_decklists d \\
               WHERE deck_format IN ('EDH','cedh')" > /tmp/ml_decklists.json

    # 2. Run import inside ingest container:
    docker compose run --rm \\
        -v /tmp/ml_decklists.json:/data/ml_decklists.json:ro \\
        ingest python import_decklists.py

"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import asyncpg

from import_utils import detect_archetype, fetch_card_details

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
INPUT_FILE   = Path(os.environ.get("DECKLIST_FILE", "/data/ml_decklists.json"))


def _asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


def normalize_deck_list(deck_list) -> list[dict]:
    """Handle both list and {cards: [...]} formats."""
    if isinstance(deck_list, list):
        return deck_list
    if isinstance(deck_list, dict):
        cards = deck_list.get("cards") or deck_list.get("entries") or []
        if isinstance(cards, list):
            return cards
    return []


def _split_partner_names(deck_name: str) -> list[str]:
    """Split partner deck names on '//' or single '/' (#147).

    cardtrak exports use both separators ('Tymna // Thrasios' and
    'Tymna / Thrasios'); single-slash names were previously skipped.
    Card names themselves never contain a bare slash.
    """
    sep = "//" if "//" in deck_name else "/"
    return [p.strip() for p in deck_name.split(sep)]


def resolve_commander(deck_name: str, name_index: dict[str, str]) -> str | None:
    """
    Commander is identified by deck_name.
    For partner decks (name1 // name2), try each part.
    Returns card_id or None.
    """
    parts = _split_partner_names(deck_name)
    for part in parts:
        cid = name_index.get(part.lower())
        if cid:
            return cid
    return None


async def build_name_index(conn) -> dict[str, str]:
    """Return {lower(name): card_id (str)} for every card in our DB."""
    rows = await conn.fetch("SELECT id::text, name FROM cards")
    return {row["name"].lower(): row["id"] for row in rows}


async def import_decks(decklists: list[dict], name_index: dict[str, str], conn) -> None:
    inserted = skipped_no_cmd = skipped_dup = 0

    for deck in decklists:
        deck_name = deck.get("deck_name", "")
        deck_url  = deck.get("deck_url") or ""
        raw_list  = deck.get("deck_list", [])
        cards     = normalize_deck_list(raw_list)

        # Commander = deck_name (is_commander flag is always False in export)
        cmd_id = resolve_commander(deck_name, name_index)
        if cmd_id is None:
            log.warning("  SKIP (commander not in DB): %s", deck_name)
            skipped_no_cmd += 1
            continue

        # Resolve maindeck cards (exclude the commander itself by name)
        cmd_names = {p.lower() for p in _split_partner_names(deck_name)}
        card_ids: list[str] = []
        unresolved = 0
        for card in cards:
            if not isinstance(card, dict):
                continue
            cname = card.get("name", "")
            if cname.lower() in cmd_names:
                continue  # skip commander card in maindeck
            cid = name_index.get(cname.lower())
            if cid:
                card_ids.append(cid)
            else:
                unresolved += 1

        if not card_ids:
            log.warning("  SKIP (no maindeck cards resolved): %s", deck_name)
            skipped_no_cmd += 1
            continue

        source = "moxfield" if "moxfield" in deck_url else \
                 "edhrec"   if "edhrec"   in deck_url else "unknown"

        # Detect archetype from card composition
        card_details = await fetch_card_details(conn, card_ids)
        arch_meta = detect_archetype(card_details)

        try:
            result = await conn.execute("""
                INSERT INTO decks (commander_id, source, source_url, card_ids, metadata)
                VALUES ($1::uuid, $2, $3, $4::uuid[], $5::jsonb)
                ON CONFLICT DO NOTHING
            """,
                cmd_id,
                source,
                deck_url or None,
                card_ids,
                json.dumps({
                    "deck_name":   deck_name,
                    "deck_format": deck.get("deck_format"),
                    "format_rank": deck.get("format_rank"),
                    "unresolved_cards": unresolved,
                    **arch_meta,
                }),
            )
            count = int(result.split()[-1])
            if count:
                inserted += 1
                log.info("  OK: %s (%d cards, %d unresolved)", deck_name, len(card_ids), unresolved)
            else:
                skipped_dup += 1
                log.debug("  DUP: %s", deck_name)

        except Exception as exc:
            log.error("  ERROR inserting %s: %s", deck_name, exc)
            continue

    log.info("Done — inserted %d, skipped %d (no commander/match), %d duplicates",
             inserted, skipped_no_cmd, skipped_dup)


async def main():
    if not INPUT_FILE.exists():
        log.error("Input file not found: %s", INPUT_FILE)
        log.error("Export with: docker exec cardtrak_db psql -U cardtrak "
                  "-d cardtrak_production -t -c "
                  "\"SELECT json_agg(row_to_json(d)) FROM ml_decklists d "
                  "WHERE deck_format IN ('EDH','cedh')\" > /tmp/ml_decklists.json")
        sys.exit(1)

    log.info("Loading %s…", INPUT_FILE)
    with INPUT_FILE.open() as f:
        decklists = json.load(f)

    if not isinstance(decklists, list):
        log.error("Expected a JSON array, got %s", type(decklists))
        sys.exit(1)

    log.info("Loaded %d decklists", len(decklists))

    dsn = _asyncpg_dsn(DATABASE_URL)
    conn = await asyncpg.connect(dsn)
    try:
        log.info("Building name index…")
        name_index = await build_name_index(conn)
        log.info("  %d cards indexed", len(name_index))
        await import_decks(decklists, name_index, conn)
    finally:
        await conn.close()

    import deck_composition_profile as dcp
    log.info("Regenerating deck composition profile → %s", dcp.OUTPUT_FILE)
    await dcp.main()


if __name__ == "__main__":
    asyncio.run(main())
