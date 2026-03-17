"""
Backfill role annotations and role_demand synergy edges for all imported decks.

For each deck in the DB, calls GET /decks/{id}/browse on the API, which:
  1. Tags each card with functional roles (ramp, draw, removal, etc.)
  2. Detects commander archetypes
  3. Writes role_demand synergy_edges from commander → role-matching cards

This is the same code path the UI triggers when a user browses a deck, so
no logic is duplicated.  Safe to re-run — all writes are idempotent.

Usage:
    docker compose run --rm ingest python backfill_roles.py

    # Against a non-default API:
    API_URL=http://localhost:8000 docker compose run --rm ingest python backfill_roles.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

API_URL = os.environ.get("API_URL", "http://api:8000").rstrip("/")

# Pause between requests to avoid hammering the API / DB
DELAY_SECONDS = 0.1


async def main() -> None:
    async with httpx.AsyncClient(timeout=60.0) as client:
        # Fetch all decks
        log.info("Fetching deck list from %s/decks …", API_URL)
        try:
            r = await client.get(f"{API_URL}/decks")
            r.raise_for_status()
        except httpx.HTTPError as exc:
            log.error("Failed to fetch deck list: %s", exc)
            sys.exit(1)

        decks = r.json()
        if not decks:
            log.warning("No decks found — import decklists first.")
            sys.exit(0)

        log.info("Found %d decks. Starting role annotation backfill…", len(decks))

        ok = skipped = errors = 0
        for i, deck in enumerate(decks, 1):
            deck_id = deck.get("deck_id")
            commander = deck.get("commander_name") or deck.get("deck_id", "?")
            if not deck_id:
                log.warning("  [%d/%d] No id in deck row — skipping", i, len(decks))
                skipped += 1
                continue

            try:
                resp = await client.get(f"{API_URL}/decks/{deck_id}/browse")
                resp.raise_for_status()
                data = resp.json()
                role_dist = data.get("role_distribution", {})
                archetypes = data.get("archetypes", [])
                archetype = data.get("archetype", "unknown")
                win_conditions = data.get("win_conditions", [])
                log.info(
                    "  [%d/%d] %s — archetype=%s win_conds=%s archetypes=%s roles=%s",
                    i, len(decks), commander,
                    archetype,
                    ",".join(win_conditions) or "none",
                    ",".join(archetypes) or "none",
                    {k: v for k, v in role_dist.items() if v},
                )
                ok += 1
            except httpx.HTTPStatusError as exc:
                log.error("  [%d/%d] %s — HTTP %s", i, len(decks), commander, exc.response.status_code)
                errors += 1
            except httpx.HTTPError as exc:
                log.error("  [%d/%d] %s — %s", i, len(decks), commander, exc)
                errors += 1

            if DELAY_SECONDS:
                await asyncio.sleep(DELAY_SECONDS)

    log.info(
        "Backfill complete — %d annotated, %d skipped, %d errors",
        ok, skipped, errors,
    )


if __name__ == "__main__":
    asyncio.run(main())
