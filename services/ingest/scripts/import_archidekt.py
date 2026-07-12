"""Import public Commander decklists from the Archidekt API (#148).

Human decklists serve two purposes: co-occurrence training signal and the
W6 harness's quota-distribution statistics (which ran on only 69 complete
decks).  This importer pulls the most-viewed public decks for a set of
priority commanders via Archidekt's open v3 API.

Politeness: one request every REQUEST_DELAY seconds, an identifying
User-Agent, and view-count ordering so we only take decks their community
already surfaced.  Decks are deduped by source_url against existing rows
(the decks table has no unique constraint on it).

Usage (from the ingest container):
    python -m scripts.import_archidekt                       # default commander list
    python -m scripts.import_archidekt --commanders "Wilhelt, the Rotcleaver"
    python -m scripts.import_archidekt --per-commander 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import asyncpg
import httpx

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from import_utils import detect_archetype, fetch_card_details  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "")

API_BASE = "https://archidekt.com/api/decks"
USER_AGENT = "mtg-pytorch-research/1.0 (github.com/cobaltroad/mtg-pytorch)"
REQUEST_DELAY = 1.5  # seconds between API calls

#: The golden harness commanders plus the docs/TODO.md priority themes.
DEFAULT_COMMANDERS = [
    "Wilhelt, the Rotcleaver",
    "Teysa Karlov",
    "Meren of Clan Nel Toth",
    "Rhys the Redeemed",
    "Adeline, Resplendent Cathar",
    "Atraxa, Praetors' Voice",
    "Wyleth, Soul of Steel",
    "Syr Gwyn, Hero of Ashvale",
    "Mizzix of the Izmagnus",
    "Aesi, Tyrant of Gyre Strait",
    "Lathril, Blade of the Elves",
    "Krenko, Mob Boss",
    "Sythis, Harvest's Hand",
    "Muldrotha, the Gravetide",
    "Yisan, the Wanderer Bard",
    "Kozilek, the Great Distortion",
    "Niv-Mizzet, Parun",
    "Hamza, Guardian of Arashin",
    "Karador, Ghost Chieftain",
    # TODO.md priority themes not already covered above
    "Vorel of the Hull Clade",
    "Tuvasa the Sunlit",
    "Gisa and Geralf",
]

#: Archidekt card categories that are not maindeck.
_EXCLUDED_CATEGORIES = {"Commander", "Maybeboard", "Sideboard", "Considering", "Wishlist"}

MIN_RESOLVED = 60          # fewer resolved maindeck cards than this → skip
MIN_SIZE, MAX_SIZE = 90, 120  # sanity band on listed deck size


def _asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def _get(client: httpx.AsyncClient, url: str, **params) -> dict | None:
    await asyncio.sleep(REQUEST_DELAY)
    try:
        r = await client.get(url, params=params or None, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        log.warning("  API error for %s: %s", url, exc)
        return None


async def search_decks(client: httpx.AsyncClient, commander: str, limit: int) -> list[dict]:
    data = await _get(
        client,
        f"{API_BASE}/v3/",
        formats=3,
        orderBy="-viewCount",
        commanderName=commander,
    )
    if not data or not data.get("results"):
        log.warning("  no Archidekt results for %s", commander)
        return []
    decks = [
        d for d in data["results"]
        if not d.get("private") and not d.get("theorycrafted")
        and MIN_SIZE <= (d.get("size") or 0) <= MAX_SIZE
    ]
    return decks[:limit]


def _maindeck_names(deck_detail: dict) -> list[str]:
    """Card names (repeated per quantity), commander/side boards excluded."""
    names: list[str] = []
    for entry in deck_detail.get("cards", []):
        categories = set(entry.get("categories") or [])
        if categories & _EXCLUDED_CATEGORIES:
            continue
        name = ((entry.get("card") or {}).get("oracleCard") or {}).get("name")
        if not name:
            continue
        names += [name] * max(1, int(entry.get("quantity") or 1))
    return names


async def build_name_index(conn) -> dict[str, str]:
    """lower(name) → card_id, plus front-face keys for '//' cards."""
    rows = await conn.fetch("SELECT id::text, name FROM cards")
    index: dict[str, str] = {}
    for row in rows:
        name = row["name"]
        index[name.lower()] = row["id"]
        if " // " in name:
            index.setdefault(name.split(" // ")[0].lower(), row["id"])
    return index


async def import_for_commander(
    client: httpx.AsyncClient,
    conn,
    commander_name: str,
    name_index: dict[str, str],
    existing_urls: set[str],
    per_commander: int,
) -> int:
    cmd_id = name_index.get(commander_name.lower())
    if not cmd_id:
        log.warning("SKIP %s — commander not in cards table", commander_name)
        return 0

    decks = await search_decks(client, commander_name, per_commander)
    log.info("%s: %d candidate decks", commander_name, len(decks))
    inserted = 0

    for meta in decks:
        url = f"https://archidekt.com/decks/{meta['id']}"
        if url in existing_urls:
            continue
        detail = await _get(client, f"{API_BASE}/{meta['id']}/")
        if not detail:
            continue

        names = _maindeck_names(detail)
        card_ids = [name_index[n.lower()] for n in names if n.lower() in name_index]
        unresolved = len(names) - len(card_ids)
        card_ids = [cid for cid in card_ids if cid != cmd_id]
        if len(card_ids) < MIN_RESOLVED:
            log.info("  SKIP %s (%d resolved)", meta.get("name", "?")[:40], len(card_ids))
            continue

        card_details = await fetch_card_details(conn, card_ids)
        arch_meta = detect_archetype(card_details)

        await conn.execute(
            """
            INSERT INTO decks (commander_id, source, source_url, card_ids, metadata)
            VALUES ($1::uuid, $2, $3, $4::uuid[], $5::jsonb)
            """,
            cmd_id,
            "archidekt",
            url,
            card_ids,
            json.dumps({
                "deck_name": meta.get("name"),
                "view_count": meta.get("viewCount"),
                "unresolved_cards": unresolved,
                **arch_meta,
            }),
        )
        existing_urls.add(url)
        inserted += 1
        log.info("  OK %s (%d cards, %d unresolved, %d views)",
                 (meta.get("name") or "?")[:45], len(card_ids), unresolved,
                 meta.get("viewCount") or 0)
    return inserted


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commanders", default="",
                        help="semicolon-separated full commander names — they contain "
                             "commas (default: built-in list)")
    parser.add_argument("--per-commander", type=int, default=15)
    args = parser.parse_args()

    commanders = [c.strip() for c in args.commanders.split(";") if c.strip()] or DEFAULT_COMMANDERS

    conn = await asyncpg.connect(_asyncpg_dsn(DATABASE_URL))
    try:
        name_index = await build_name_index(conn)
        rows = await conn.fetch("SELECT source_url FROM decks WHERE source_url IS NOT NULL")
        existing_urls = {r["source_url"] for r in rows}
        log.info("name index: %d cards; existing deck urls: %d", len(name_index), len(existing_urls))

        total = 0
        async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
            for commander in commanders:
                total += await import_for_commander(
                    client, conn, commander, name_index, existing_urls, args.per_commander
                )
        log.info("Done — %d decks imported", total)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
