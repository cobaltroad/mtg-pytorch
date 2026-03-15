"""
Batch-import Moxfield Commander decklist exports into the mtg-pytorch decks table.

Input:  A folder of .txt files exported from Moxfield (one deck per file).
Output: rows inserted into the decks table (commander_id, source, card_ids[])

Supported decklist formats
--------------------------
1. Sectioned (standard Moxfield export): labelled sections Commander /
   Mainboard. Only those two sections are used; Sideboard, Maybeboard, etc.
   are ignored.

2. Headerless (plain list, commander last): all maindeck cards listed first,
   then a blank line, then the commander. Common from other tools or when
   lists are assembled manually.

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


def _parse_card_line(raw_line: str) -> tuple[str, int] | None:
    """Parse a card line into (name, qty). Returns None if not a card line."""
    line = raw_line.strip()
    if not line or not (line[0].isdigit() or _QTY_RE.match(line)):
        return None
    qty_match = _QTY_RE.match(line)
    qty = int(qty_match.group().strip().rstrip("x")) if qty_match else 1
    name = _QTY_RE.sub("", line).strip()
    # Strip set/collector annotations Moxfield sometimes appends: " (MH2) 123"
    name = re.sub(r"\s+\([A-Z0-9]{2,6}\)\s*\d*$", "", name).strip()
    return (name, qty) if name else None


def parse_moxfield_txt(text: str) -> tuple[list[str], list[str]]:
    """
    Parse a decklist export into (commanders, maindeck).

    Handles two formats:

    1. Sectioned (standard Moxfield export):
          Commander
          1 Sauron, the Dark Lord

          Mainboard
          1 Sol Ring
          ...

    2. Headerless (plain list, commander last after a blank line):
          1 Sol Ring
          1 Arcane Signet
          ...

          1 Sauron, the Dark Lord

    Returns:
        commanders : list of card names (usually 1, occasionally 2 for partners)
        maindeck   : list of card names (may contain duplicates for qty > 1)
    """
    commanders: list[str] = []
    maindeck:   list[str] = []

    current_section: str | None = None
    found_section_header = False

    for raw_line in text.splitlines():
        line = raw_line.strip()

        # Empty line resets section (Moxfield separates sections with blank lines)
        if not line:
            current_section = None
            continue

        # Section header — no quantity prefix, not a card line
        if not _QTY_RE.match(line) and not line[0].isdigit():
            found_section_header = True
            slug = line.lower().rstrip(":")
            if slug in CMD_SECTIONS:
                current_section = "commander"
            elif slug in MAIN_SECTIONS:
                current_section = "main"
            elif slug in SKIP_SECTIONS:
                current_section = "skip"
            # else: unknown header — ignore until next blank line
            continue

        if current_section == "skip":
            continue

        parsed = _parse_card_line(raw_line)
        if parsed is None:
            continue
        name, qty = parsed

        if current_section == "commander":
            commanders.append(name)
        elif current_section == "main":
            maindeck.extend([name] * qty)
        elif current_section is None and not found_section_header:
            # Headerless format: accumulate everything; we'll split after
            maindeck.extend([name] * qty)

    # ── Trailing-commander fallback ───────────────────────────────────────────
    # If no commander was found yet, check whether the file ends with a
    # blank-line-separated group that names the commander.  This handles:
    #   - Fully headerless files (no section headers at all)
    #   - Files with non-Commander section headers (e.g. SIDEBOARD:) whose
    #     commander is still listed last after a blank line
    if not commanders and maindeck:
        lines = [l.strip() for l in text.splitlines()]
        # Find the last blank line
        last_blank = -1
        for i, l in enumerate(lines):
            if not l:
                last_blank = i
        if last_blank != -1:
            tail_lines = lines[last_blank + 1:]
            tail_cards: list[str] = []
            for tl in tail_lines:
                parsed = _parse_card_line(tl)
                if parsed:
                    tail_cards.extend([parsed[0]] * parsed[1])
            if tail_cards:
                # Tail is the commander block; strip those from maindeck
                tail_set = set(tail_cards)
                # Remove tail cards from the end of maindeck
                trimmed: list[str] = []
                for name in reversed(maindeck):
                    if name in tail_set and name not in trimmed:
                        tail_set.discard(name)
                        continue
                    trimmed.append(name)
                maindeck = list(reversed(trimmed))
                commanders = tail_cards

    return commanders, maindeck


def _asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def build_name_index(conn) -> dict[str, str]:
    """Return {lower(name): card_id (str)} for every card in our DB.

    DFC cards are stored as "Front // Back" (the MTGJSON key format).  Decklist
    files always use just the front-face name, so we also index the front face
    alone so lookups like "Poppet Stitcher" resolve to "Poppet Stitcher // Poppet
    Factory" correctly.  The full " // " name takes precedence if it appears in
    a decklist verbatim.
    """
    rows = await conn.fetch("SELECT id::text, name FROM cards")
    index: dict[str, str] = {}
    for row in rows:
        full = row["name"]
        cid  = row["id"]
        index[full.lower()] = cid
        # Also index front face alone and slash variants for DFC / Room cards.
        # DB stores: "Poppet Stitcher // Poppet Factory" (MTGJSON key format)
        # Decklists use any of: "Poppet Stitcher", "Front / Back", "Front/Back"
        if " // " in full:
            front = full.split(" // ")[0].strip()
            back  = full.split(" // ")[1].strip()
            index.setdefault(front.lower(), cid)
            index.setdefault(f"{front.lower()} / {back.lower()}", cid)
            index.setdefault(f"{front.lower()}/{back.lower()}", cid)
    return index


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
