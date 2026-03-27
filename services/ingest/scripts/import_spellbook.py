"""
Import Commander Spellbook combo variants into the combo_packages table.

Fetches all variants with status=OK, legal in commander, and no spoiler cards,
then matches each card to our local DB via oracle_id.  Cards not found locally
are stored with card_id=NULL so the combo is still recorded.  Template slots
(generic requirements like "any sacrifice outlet") are stored with
is_template=TRUE.

Usage
-----
    # Full import
    docker compose run --rm ingest python import_spellbook.py

    # Dry-run (no DB writes)
    SPELLBOOK_DRY_RUN=1 docker compose run --rm ingest python import_spellbook.py

    # Limit for testing
    SPELLBOOK_LIMIT=200 docker compose run --rm ingest python import_spellbook.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from uuid import UUID

import asyncpg
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATABASE_URL    = os.environ.get("DATABASE_URL", "")
DRY_RUN         = os.environ.get("SPELLBOOK_DRY_RUN", "").strip() not in ("", "0")
SPELLBOOK_LIMIT = int(os.environ.get("SPELLBOOK_LIMIT", "0"))  # 0 = no limit

_BASE_URL   = "https://backend.commanderspellbook.com"
_PAGE_SIZE  = 100

# Features that map to a higher package weight during scoring
WIN_FEATURES      = {"Win the game", "Exile all cards from target player's library"}
INFINITE_FEATURES = {
    "Infinite mana", "Infinite damage", "Infinite draws",
    "Infinite tokens", "Infinite life", "Infinite storm count",
    "Infinite combat phases", "Infinite ETB", "Infinite death triggers",
}


def _asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")



async def _build_oracle_index(conn) -> dict[str, tuple[str, str]]:
    """Return {oracle_id_str: (card_id_str, card_name)} for every card in our DB."""
    rows = await conn.fetch("SELECT id::text, oracle_id::text, name FROM cards")
    return {row["oracle_id"]: (row["id"], row["name"]) for row in rows if row["oracle_id"]}


async def _upsert_variant(conn, variant: dict, oracle_index: dict[str, tuple[str, str]]) -> str:
    """
    Insert or update one Spellbook variant.

    Returns "ok" | "dup" | "error".
    """
    spellbook_id = variant["id"]
    identity     = variant.get("identity") or "C"
    produces     = [p["feature"]["name"] for p in (variant.get("produces") or [])]
    combo_ids    = [c["id"] for c in (variant.get("of") or [])]

    pkg_id: str = await conn.fetchval("""
        INSERT INTO combo_packages (
            spellbook_id, combo_ids, identity, produces,
            description, easy_prerequisites, notable_prerequisites,
            mana_needed, mana_value_needed, popularity, bracket_tag,
            legal_commander, spoiler
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, TRUE, FALSE)
        ON CONFLICT (spellbook_id) DO UPDATE SET
            combo_ids             = EXCLUDED.combo_ids,
            produces              = EXCLUDED.produces,
            description           = EXCLUDED.description,
            easy_prerequisites    = EXCLUDED.easy_prerequisites,
            notable_prerequisites = EXCLUDED.notable_prerequisites,
            mana_needed           = EXCLUDED.mana_needed,
            mana_value_needed     = EXCLUDED.mana_value_needed,
            popularity            = EXCLUDED.popularity,
            bracket_tag           = EXCLUDED.bracket_tag,
            updated_at            = NOW()
        RETURNING id::text
    """,
        spellbook_id,
        combo_ids or None,
        identity,
        produces,
        variant.get("description"),
        variant.get("easyPrerequisites"),
        variant.get("notablePrerequisites"),
        variant.get("manaNeeded"),
        variant.get("manaValueNeeded"),
        variant.get("popularity"),
        variant.get("bracketTag"),
    )

    # Insert card slots from uses[]
    for use in (variant.get("uses") or []):
        card_data   = use.get("card") or {}
        card_name   = card_data.get("name", "")
        raw_oid     = card_data.get("oracleId")
        oracle_str  = str(raw_oid) if raw_oid else None

        card_id: str | None  = None
        oracle_id: str | None = oracle_str
        if oracle_str and oracle_str in oracle_index:
            card_id, _ = oracle_index[oracle_str]

        await conn.execute("""
            INSERT INTO combo_package_cards (
                combo_package_id, card_id, spellbook_card_name, oracle_id,
                must_be_commander, quantity, zone_locations, battlefield_state,
                is_template, template_name
            ) VALUES ($1, $2::uuid, $3, $4::uuid, $5, $6, $7, $8, FALSE, NULL)
            ON CONFLICT (combo_package_id, spellbook_card_name) DO NOTHING
        """,
            pkg_id,
            card_id,
            card_name,
            oracle_id,
            use.get("mustBeCommander", False),
            use.get("quantity", 1),
            use.get("zoneLocations") or [],
            use.get("battlefieldCardState") or None,
        )

    # Insert template slots from requires[]
    for req in (variant.get("requires") or []):
        tmpl       = req.get("template") or {}
        tmpl_name  = tmpl.get("name", "Unknown template")

        await conn.execute("""
            INSERT INTO combo_package_cards (
                combo_package_id, card_id, spellbook_card_name, oracle_id,
                must_be_commander, quantity, zone_locations, battlefield_state,
                is_template, template_name
            ) VALUES ($1, NULL, $2, NULL, FALSE, $3, $4, $5, TRUE, $6)
            ON CONFLICT (combo_package_id, spellbook_card_name) DO NOTHING
        """,
            pkg_id,
            f"[template] {tmpl_name}",
            req.get("quantity", 1),
            req.get("zoneLocations") or [],
            req.get("battlefieldCardState") or None,
            tmpl_name,
        )

    return "ok"


async def main() -> None:
    if not DATABASE_URL:
        log.error("DATABASE_URL not set")
        sys.exit(1)

    if DRY_RUN:
        log.info("DRY-RUN mode — no database writes")

    # Build DB connection and oracle index before fetching (fail fast on bad DSN)
    conn = None
    oracle_index: dict[str, tuple[str, str]] = {}
    if not DRY_RUN:
        dsn  = _asyncpg_dsn(DATABASE_URL)
        conn = await asyncpg.connect(dsn)
        log.info("Building oracle_id index from local card DB…")
        oracle_index = await _build_oracle_index(conn)
        log.info("  %d oracle IDs indexed", len(oracle_index))

    log.info("Fetching and importing variants from Commander Spellbook…")
    base_url = f"{_BASE_URL}/variants/"
    offset = 0
    page = 0
    ok = errors = total_fetched = 0

    try:
        async with httpx.AsyncClient() as client:
            while True:
                resp = await client.get(
                    base_url,
                    params={"limit": _PAGE_SIZE, "offset": offset, "ordering": "id"},
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                results = data.get("results", [])
                page += 1

                if not results:
                    break

                for v in results:
                    if v.get("status") != "OK":
                        continue
                    if v.get("spoiler"):
                        continue
                    legalities = v.get("legalities") or {}
                    if not legalities.get("commander"):
                        continue

                    total_fetched += 1

                    if DRY_RUN:
                        if total_fetched <= 5:
                            produces = [p["feature"]["name"] for p in (v.get("produces") or [])]
                            cards    = [u["card"]["name"] for u in (v.get("uses") or []) if u.get("card")]
                            log.info("  DRY-RUN variant %s: %s → %s", v["id"], cards, produces)
                        continue

                    try:
                        await _upsert_variant(conn, v, oracle_index)
                        ok += 1
                    except Exception as exc:
                        errors += 1
                        log.warning("  ERROR on variant %s: %s", v.get("id"), exc)

                    if SPELLBOOK_LIMIT and total_fetched >= SPELLBOOK_LIMIT:
                        log.info("SPELLBOOK_LIMIT=%d reached", SPELLBOOK_LIMIT)
                        break

                if page % 10 == 0 or (SPELLBOOK_LIMIT and total_fetched >= SPELLBOOK_LIMIT):
                    log.info("  … page %d | %d eligible | %d imported | %d errors",
                             page, total_fetched, ok, errors)

                if SPELLBOOK_LIMIT and total_fetched >= SPELLBOOK_LIMIT:
                    break
                if len(results) < _PAGE_SIZE:
                    break

                offset += _PAGE_SIZE

    finally:
        if conn:
            await conn.close()

    if DRY_RUN:
        log.info("DRY-RUN complete — %d variants would be imported", total_fetched)
    else:
        log.info("Done — %d imported/updated, %d errors (from %d eligible variants across %d pages)",
                 ok, errors, total_fetched, page)


if __name__ == "__main__":
    asyncio.run(main())
