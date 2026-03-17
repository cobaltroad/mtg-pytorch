"""
Decklist parsing and import utilities for the API.

Reuses the same parsing logic as services/ingest/import_moxfield.py so that
pasted text from the UI is handled identically to file-based imports.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ── Alias table (same file as ingest service uses) ───────────────────────────
_ALIAS_FILE = Path("/app/card_name_aliases.csv")

def _load_aliases() -> dict[str, str]:
    aliases: dict[str, str] = {}
    if not _ALIAS_FILE.exists():
        return aliases
    import csv
    with _ALIAS_FILE.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            alt   = row["alternate_name"].strip()
            oracle = row["oracle_name"].strip()
            if alt and oracle:
                aliases[alt.lower()] = oracle
    return aliases

NAME_ALIASES: dict[str, str] = _load_aliases()

# ── Section labels ────────────────────────────────────────────────────────────
MAIN_SECTIONS = {"mainboard", "deck"}
CMD_SECTIONS  = {"commander"}
SKIP_SECTIONS = {"sideboard", "maybeboard", "commanders", "companion",
                 "tokens", "attractions"}

_QTY_RE = re.compile(r"^\d+x?\s+")


def _parse_card_line(raw_line: str) -> tuple[str, int] | None:
    line = raw_line.strip()
    if not line or not (line[0].isdigit() or _QTY_RE.match(line)):
        return None
    qty_match = _QTY_RE.match(line)
    qty  = int(qty_match.group().strip().rstrip("x")) if qty_match else 1
    name = _QTY_RE.sub("", line).strip()
    name = re.sub(r"\s+\([A-Z0-9]{2,6}\)\s*\d*$", "", name).strip()
    name = NAME_ALIASES.get(name.lower(), name)
    return (name, qty) if name else None


def parse_moxfield_txt(text: str) -> tuple[list[str], list[str]]:
    """Return (commanders, maindeck) from raw Moxfield export text."""
    commanders: list[str] = []
    maindeck:   list[str] = []
    current_section: str | None = None
    found_section_header = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            current_section = None
            continue
        if not _QTY_RE.match(line) and not line[0].isdigit():
            found_section_header = True
            slug = line.lower().rstrip(":")
            if slug in CMD_SECTIONS:
                current_section = "commander"
            elif slug in MAIN_SECTIONS:
                current_section = "main"
            elif slug in SKIP_SECTIONS:
                current_section = "skip"
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
            maindeck.extend([name] * qty)

    # Trailing-commander fallback (headerless / SIDEBOARD-only files)
    if not commanders and maindeck:
        lines = [l.strip() for l in text.splitlines()]
        last_blank = -1
        for i, l in enumerate(lines):
            if not l:
                last_blank = i
        if last_blank != -1:
            tail_cards: list[str] = []
            for tl in lines[last_blank + 1:]:
                parsed = _parse_card_line(tl)
                if parsed:
                    tail_cards.extend([parsed[0]] * parsed[1])
            if tail_cards:
                tail_set = set(tail_cards)
                trimmed: list[str] = []
                for name in reversed(maindeck):
                    if name in tail_set and name not in trimmed:
                        tail_set.discard(name)
                        continue
                    trimmed.append(name)
                maindeck = list(reversed(trimmed))
                commanders = tail_cards

    return commanders, maindeck


async def build_name_index(db: AsyncSession) -> dict[str, str]:
    """Return {lower(name): card_id (str)} for every card, including DFC variants."""
    rows = (await db.execute(text("SELECT id::text, name FROM cards"))).fetchall()
    index: dict[str, str] = {}
    for row in rows:
        full = row[1]
        cid  = row[0]
        index[full.lower()] = cid
        if " // " in full:
            front = full.split(" // ")[0].strip()
            back  = full.split(" // ")[1].strip()
            index.setdefault(front.lower(), cid)
            index.setdefault(f"{front.lower()} / {back.lower()}", cid)
            index.setdefault(f"{front.lower()}/{back.lower()}", cid)
    return index


async def import_decklist_text(
    raw_text: str,
    deck_name: str,
    db: AsyncSession,
    source: str = "moxfield",
) -> dict:
    """
    Parse and import a single pasted decklist.

    Returns a result dict with keys:
        ok (bool), commander (str|None), cards_imported (int),
        unresolved (list[str]), message (str), duplicate (bool)
    """
    commanders, maindeck = parse_moxfield_txt(raw_text)

    if not commanders:
        return {"ok": False, "commander": None, "cards_imported": 0,
                "unresolved": [], "duplicate": False,
                "message": "No commander section found. Add a 'Commander' header or put the commander last after a blank line."}

    name_index = await build_name_index(db)

    # Resolve commander
    cmd_id:   str | None = None
    cmd_name: str | None = None
    for cname in commanders:
        cid = name_index.get(cname.lower())
        if cid:
            cmd_id   = cid
            cmd_name = cname
            break

    if cmd_id is None:
        return {"ok": False, "commander": commanders[0], "cards_imported": 0,
                "unresolved": commanders, "duplicate": False,
                "message": f"Commander not found in database: {', '.join(commanders)}"}

    # Resolve maindeck
    cmd_name_set = {c.lower() for c in commanders}
    card_ids: list[str] = []
    unresolved_names: list[str] = []

    for cname in maindeck:
        if cname.lower() in cmd_name_set:
            continue
        cid = name_index.get(cname.lower())
        if cid:
            card_ids.append(cid)
        else:
            if cname not in unresolved_names:
                unresolved_names.append(cname)

    if not card_ids:
        return {"ok": False, "commander": cmd_name, "cards_imported": 0,
                "unresolved": unresolved_names, "duplicate": False,
                "message": "No maindeck cards resolved."}

    result = await db.execute(text("""
        INSERT INTO decks (commander_id, source, source_url, card_ids, metadata)
        VALUES (CAST(:cmd_id AS uuid), :source, NULL, CAST(:card_ids AS uuid[]), CAST(:meta AS jsonb))
        ON CONFLICT DO NOTHING
        RETURNING id::text
    """), {
        "cmd_id":   cmd_id,
        "source":   source,
        "card_ids": card_ids,
        "meta": json.dumps({
            "deck_name":        deck_name,
            "commanders":       commanders,
            "unresolved_cards": len(unresolved_names),
        }),
    })
    await db.commit()

    inserted_row = result.fetchone()
    duplicate    = (inserted_row is None)
    deck_id      = inserted_row[0] if inserted_row else None

    # ── First-pass analysis on new decks ─────────────────────────────────────
    analysis: dict | None = None
    if deck_id:
        try:
            from ops.deck_browser import get_deck_with_roles
            analysis = await get_deck_with_roles(db, deck_id)
        except Exception:
            pass  # analysis is optional; import still succeeds

    return {
        "ok":             True,
        "commander":      cmd_name,
        "cards_imported": len(card_ids),
        "unresolved":     unresolved_names,
        "duplicate":      duplicate,
        "deck_id":        deck_id,
        "analysis":       analysis,  # first-pass role + archetype parse
        "message": (
            f"Duplicate — deck already in database."
            if duplicate else
            f"Imported {len(card_ids)} cards for {cmd_name}."
            + (f" {len(unresolved_names)} card(s) unresolved." if unresolved_names else "")
        ),
    }
