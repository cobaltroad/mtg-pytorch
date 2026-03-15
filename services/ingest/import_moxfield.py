"""
Batch-import Moxfield Commander decklist exports into the mtg-pytorch decks table.

Input:  A folder of .txt files exported from Moxfield (one deck per file).
Output: rows inserted into the decks table (commander_id, source, card_ids[])

Moxfield export format
----------------------
The file is divided into labelled sections. Only 'Commander' and 'Mainboard'
(sometimes 'Deck') are used; everything else (Sideboard, Maybeboard, etc.)
is ignored.

    Commander
    1 Wilhelt, the Rotcleaver

    Mainboard
    1 Gravecrawler
    1 Cryptbreaker
    ...

Double-faced / partner commanders appear as two consecutive Commander lines:

    Commander
    1 Tymna the Weaver
    1 Thrasios, Triton Hero

Quantity prefixes (e.g. "1 ") are stripped; only the card name is used.
Cards not found in the DB (new printings, tokens, art cards) are skipped.
Decks where no commander resolves are skipped entirely.

Usage (from repo root)
----------------------
    # Drop .txt exports into /tmp/moxfield/, then:
    docker compose run --rm \\
        -v /tmp/moxfield:/data/moxfield:ro \\
        ingest python import_moxfield.py

    # Custom folder:
    MOXFIELD_DIR=/data/my_decks docker compose run --rm ingest python import_moxfield.py

    # Dry-run (parse only, no DB writes):
    MOXFIELD_DRY_RUN=1 docker compose run --rm ingest python import_moxfield.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path

import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATABASE_URL  = os.environ.get("DATABASE_URL", "")
MOXFIELD_DIR  = Path(os.environ.get("MOXFIELD_DIR", "/data/moxfield"))
DRY_RUN       = os.environ.get("MOXFIELD_DRY_RUN", "").strip() not in ("", "0")

# Sections whose card lines go into the maindeck
MAIN_SECTIONS = {"mainboard", "deck"}
# Sections whose card lines go into commander slot
CMD_SECTIONS  = {"commander"}
# Sections to skip entirely
SKIP_SECTIONS = {"sideboard", "maybeboard", "commanders", "companion",
                 "tokens", "attractions"}

_QTY_RE = re.compile(r"^\d+x?\s+")  # strip leading "1 " or "2x "


def parse_moxfield_txt(text: str) -> tuple[list[str], list[str]]:
    """
    Parse a Moxfield export.

    Returns:
        commanders : list of card names (usually 1, occasionally 2 for partners)
        maindeck   : list of card names (may contain duplicates for qty > 1)
    """
    commanders: list[str] = []
    maindeck:   list[str] = []

    current_section: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()

        # Empty line resets section (Moxfield separates sections with blank lines)
        if not line:
            current_section = None
            continue

        # Section header — no quantity prefix, not a card line
        lower = line.lower()
        if not _QTY_RE.match(line) and not line[0].isdigit():
            # Treat the whole line as a potential section name
            slug = lower.rstrip(":")
            if slug in CMD_SECTIONS:
                current_section = "commander"
            elif slug in MAIN_SECTIONS:
                current_section = "main"
            elif slug in SKIP_SECTIONS:
                current_section = "skip"
            # else: unknown header — ignore until next blank line
            continue

        if current_section == "skip" or current_section is None:
            continue

        # Strip quantity prefix to get the card name
        name = _QTY_RE.sub("", line).strip()
        # Strip set/collector annotations Moxfield sometimes appends: " (MH2) 123"
        name = re.sub(r"\s+\([A-Z0-9]{2,6}\)\s*\d*$", "", name).strip()
        if not name:
            continue

        qty_match = _QTY_RE.match(raw_line.strip())
        qty = int(qty_match.group().strip().rstrip("x")) if qty_match else 1

        if current_section == "commander":
            commanders.append(name)
        elif current_section == "main":
            maindeck.extend([name] * qty)

    return commanders, maindeck


def _asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def build_name_index(conn) -> dict[str, str]:
    """Return {lower(name): card_id (str)} for every card in our DB."""
    rows = await conn.fetch("SELECT id::text, name FROM cards")
    return {row["name"].lower(): row["id"] for row in rows}


async def import_file(
    path: Path,
    name_index: dict[str, str],
    conn,
) -> tuple[str, str]:
    """
    Import a single .txt file.

    Returns:
        ("ok" | "dup" | "skip", reason_string)
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    commanders, maindeck = parse_moxfield_txt(text)

    if not commanders:
        return "skip", "no commander section found"

    # Resolve commander — use first name that matches
    cmd_id: str | None = None
    cmd_name: str | None = None
    for cname in commanders:
        cid = name_index.get(cname.lower())
        if cid:
            cmd_id   = cid
            cmd_name = cname
            break

    if cmd_id is None:
        return "skip", f"commander not in DB: {', '.join(commanders)}"

    # Resolve maindeck (exclude commander names)
    cmd_name_set = {c.lower() for c in commanders}
    card_ids: list[str] = []
    unresolved = 0
    for cname in maindeck:
        if cname.lower() in cmd_name_set:
            continue  # commander listed in mainboard — skip
        cid = name_index.get(cname.lower())
        if cid:
            card_ids.append(cid)
        else:
            unresolved += 1

    if not card_ids:
        return "skip", "no maindeck cards resolved"

    deck_name = path.stem  # filename without extension as deck label

    if DRY_RUN:
        log.info("  DRY-RUN: %s → %s (%d cards, %d unresolved)",
                 path.name, cmd_name, len(card_ids), unresolved)
        return "ok", "dry-run"

    result = await conn.execute("""
        INSERT INTO decks (commander_id, source, source_url, card_ids, metadata)
        VALUES ($1::uuid, $2, $3, $4::uuid[], $5::jsonb)
        ON CONFLICT DO NOTHING
    """,
        cmd_id,
        "moxfield",
        None,           # no URL available from a local file export
        card_ids,
        json.dumps({
            "deck_name":        deck_name,
            "file":             path.name,
            "commanders":       commanders,
            "unresolved_cards": unresolved,
        }),
    )
    count = int(result.split()[-1])
    return ("ok" if count else "dup"), (
        f"{cmd_name} — {len(card_ids)} cards, {unresolved} unresolved"
        if count else "duplicate"
    )


async def main() -> None:
    if not MOXFIELD_DIR.is_dir():
        log.error("Directory not found: %s", MOXFIELD_DIR)
        log.error("Set MOXFIELD_DIR or mount your .txt files at /data/moxfield")
        sys.exit(1)

    txt_files = sorted(MOXFIELD_DIR.glob("*.txt"))
    if not txt_files:
        log.warning("No .txt files found in %s", MOXFIELD_DIR)
        sys.exit(0)

    log.info("Found %d .txt file(s) in %s", len(txt_files), MOXFIELD_DIR)
    if DRY_RUN:
        log.info("DRY-RUN mode — no database writes")

    dsn  = _asyncpg_dsn(DATABASE_URL)
    conn = await asyncpg.connect(dsn)
    try:
        log.info("Building name index…")
        name_index = await build_name_index(conn)
        log.info("  %d cards indexed", len(name_index))

        ok = skipped = dupes = 0
        for path in txt_files:
            status, reason = await import_file(path, name_index, conn)
            if status == "ok":
                ok += 1
                log.info("  OK:   %s  (%s)", path.name, reason)
            elif status == "dup":
                dupes += 1
                log.debug(" DUP:  %s", path.name)
            else:
                skipped += 1
                log.warning("  SKIP: %s — %s", path.name, reason)

    finally:
        await conn.close()

    log.info("Done — %d inserted, %d skipped, %d duplicates", ok, skipped, dupes)


if __name__ == "__main__":
    asyncio.run(main())
