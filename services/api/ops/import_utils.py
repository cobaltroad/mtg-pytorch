"""
Decklist parsing and import utilities for the API.

Reuses the same parsing logic as services/ingest/import_moxfield.py so that
pasted text from the UI is handled identically to file-based imports.

``detect_archetype()`` is a copy of the function in
``services/ingest/import_utils.py``; the two containers do not share a Python
path so the logic is duplicated here.  Keep both in sync when updating
heuristics.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

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

# ── Archetype detection (copy of services/ingest/import_utils.py) ─────────────
# Keep in sync with the ingest service version when updating heuristics.

# Role-pattern lookup, assembled from ROLE_PATTERNS-style regexes inline so
# this module stays self-contained (no ingest service import path available).

_RAMP_RES = [
    re.compile(r"\{T\}:\s*[Aa]dd", re.IGNORECASE),
    re.compile(r"\badd \{[WUBRGCXS]\}", re.IGNORECASE),
    re.compile(r"\badd \{\d+\}", re.IGNORECASE),
    re.compile(r"\badd [a-z]+ mana\b", re.IGNORECASE),
    re.compile(r"\badd mana (of|in) (any|one|two|three)", re.IGNORECASE),
    re.compile(r"search your library.{0,30}for (a |an |up to \w+ )?"
               r"(plains|island|swamp|mountain|forest|snow-covered|basic land)"
               r".{0,100}(battlefield|into play|to your hand)", re.IGNORECASE),
    re.compile(r"put (a|one|two|an?|the) (basic )?land.{0,40}(onto|into) (the )?battlefield", re.IGNORECASE),
    re.compile(r"play (one|two|three|x|an?)? ?additional lands?", re.IGNORECASE),
]

_DRAW_RES = [
    re.compile(r"\bdraw (a card|one card|two cards|three cards|four cards|five cards"
               r"|six cards|seven cards|x cards?|\d+ cards?)\b", re.IGNORECASE),
    re.compile(r"\bdraw (a card|cards?).{0,40}discard (a card|cards?)"
               r"|\bdiscard.{0,30}draw (a card|cards?)\b", re.IGNORECASE),
    re.compile(r"\bwhenever\b.{0,120}draws? (a card|\d+ cards?|cards?)", re.IGNORECASE),
    re.compile(r"at the beginning of.{0,80}draws? (a card|\d+ cards?|cards?|an additional card)", re.IGNORECASE),
    re.compile(r"\{[^}]+\}[^:]{0,80}:\s*.{0,60}draw (a card|\d+ cards?|cards?)", re.IGNORECASE),
    re.compile(r"each (player|opponent).{0,60}draws? (cards?|a card|\d+ cards?)", re.IGNORECASE),
]

_REMOVAL_RES = [
    re.compile(r"destroy target (\w+ )?(creature|permanent|artifact|enchantment"
               r"|planeswalker|nonland permanent|land|token)", re.IGNORECASE),
    re.compile(r"exile target (\w+ )?(creature|permanent|artifact|enchantment"
               r"|planeswalker|nonland permanent|land|token)", re.IGNORECASE),
    re.compile(r"destroy (all|each) (creatures?|permanents?|nonland permanents?"
               r"|artifacts?|enchantments?|tokens?)", re.IGNORECASE),
    re.compile(r"exile (all|each) (creatures?|permanents?|nonland permanents?"
               r"|artifacts?|enchantments?|tokens?)", re.IGNORECASE),
    re.compile(r"deals? \w+ damage to (all|each) creature", re.IGNORECASE),
    re.compile(r"return (all|each) (nonland permanents?|permanents?|creatures?)"
               r".{0,40}(to (its|their|your).{0,10}hand|to their owners'? hand)", re.IGNORECASE),
    re.compile(r"deals? \w+ damage to (any target|target (creature|player|opponent|planeswalker))",
               re.IGNORECASE),
    re.compile(r"return target (creature|permanent|nonland permanent|artifact|enchantment"
               r"|planeswalker).{0,40}(to its owner's hand|to their owner's hand|to your hand)",
               re.IGNORECASE),
]

_TUTOR_RES = [
    re.compile(
        r"search your library( and/or \w+)? for (a |an |up to (one|two|three) )?"
        r"(card|creature card|artifact card|land card|enchantment card"
        r"|instant card|sorcery card|legendary card|basic land card"
        r"|plains|island|swamp|mountain|forest|planeswalker card)",
        re.IGNORECASE,
    ),
]

_INTERACTION_RES = [
    re.compile(r"counter target spell\b", re.IGNORECASE),
    re.compile(r"counter target (noncreature|creature|instant|sorcery|enchantment"
               r"|artifact|legendary).{0,40}\bspell\b", re.IGNORECASE),
    re.compile(r"counter target spell.{0,80}unless", re.IGNORECASE),
]

_TOKEN_RES = [
    re.compile(r"create (a|an|one|two|three|four|five|six|x|\d+).{0,50}tokens?", re.IGNORECASE),
    re.compile(r"put (a|an|one|two|three|\d+).{0,50}token.{0,30}(onto|into) (the )?battlefield",
               re.IGNORECASE),
]

_RECURSION_RES = [
    re.compile(
        r"return (target )?.{0,60}card from (your|a|any) graveyard"
        r".{0,60}(to (your hand|the battlefield|battlefield))",
        re.IGNORECASE,
    ),
    re.compile(
        r"put.{0,30}from (your|a|the) graveyard.{0,40}(onto|into|to) (the )?battlefield",
        re.IGNORECASE,
    ),
    re.compile(r"enchant creature card in (a|the) graveyard", re.IGNORECASE),
    re.compile(r"when.{0,60}dies.{0,60}return (it|that card|target creature)", re.IGNORECASE),
]

_ANTHEM_RES = [
    re.compile(
        r"(creatures? (tokens? )?(you control|in your command zone)"
        r"|other creatures you control) get \+\d+/[+\d]",
        re.IGNORECASE,
    ),
    re.compile(r"each (creature you control|of your creatures) gets? \+\d+/[+\d]", re.IGNORECASE),
    re.compile(r"(gets?|get) \+\d+/[+\-\d]+.{0,50}for each (other|creature)", re.IGNORECASE),
]

_WIN_COND_ROLE_RES = [
    re.compile(r"\binfect\b", re.IGNORECASE),
    re.compile(r"\btoxic \d\b", re.IGNORECASE),
    re.compile(r"\bpoison counter", re.IGNORECASE),
    re.compile(r"(you |the )?(wins?|win) the game\b", re.IGNORECASE),
    re.compile(r"that player (loses|lost) the game\b", re.IGNORECASE),
    re.compile(r"each (opponent|player) loses the game\b", re.IGNORECASE),
]

# Stax, punisher, and spellslinger patterns
_STAX_RES = [
    re.compile(r"opponents? can't", re.IGNORECASE),
    re.compile(r"skip (your|their) (untap|upkeep|draw)", re.IGNORECASE),
    re.compile(r"pay \{[0-9X]+\} more to (cast|play)", re.IGNORECASE),
    re.compile(r"spells? costs? \{[0-9X]+\} more", re.IGNORECASE),
    re.compile(r"unless (its controller|they|that player) pays? \{", re.IGNORECASE),
    re.compile(r"each (player|opponent) (can't|doesn't untap|skips)", re.IGNORECASE),
    re.compile(r"players? can't (cast|play|untap|draw)", re.IGNORECASE),
]

_PUNISHER_RES = [
    re.compile(
        r"whenever (an opponent|each opponent).{0,80}"
        r"(loses? \d+ life|takes? \d+ damage)",
        re.IGNORECASE,
    ),
    re.compile(r"each opponent loses \d+ life", re.IGNORECASE),
    re.compile(r"each player loses \d+ life", re.IGNORECASE),
    re.compile(
        r"whenever (a player|an opponent) "
        r"(casts? a spell|draws? (a card|\d+ cards?)|gains? life|plays? a land)"
        r".{0,80}(loses? \d+ life|takes? \d+ damage)",
        re.IGNORECASE,
    ),
    re.compile(
        r"at the beginning of (each|your) (opponent's|upkeep).{0,80}loses? \d+ life",
        re.IGNORECASE,
    ),
]

_SPELLSLINGER_RE = re.compile(
    r"\b(magecraft|storm|prowess|whenever you cast (an instant|a sorcery|a spell))\b",
    re.IGNORECASE,
)

_WIN_CON_RES: dict[str, re.Pattern] = {
    "infect": re.compile(r"\b(infect|toxic \d)\b", re.IGNORECASE),
    "aristocrats": re.compile(
        r"(whenever (a|another) creature (dies|is put into (a|your) graveyard)"
        r".{0,80}(you gain|you lose|you draw|deal \d+ damage))"
        r"|(whenever you sacrifice a creature.{0,80}"
        r"(you gain|you lose|you draw|deal \d+ damage))",
        re.IGNORECASE | re.DOTALL,
    ),
    "group_slug": re.compile(
        r"(each opponent loses \d+ life"
        r"|deals? \d+ damage to each (opponent|player)"
        r"|each (opponent|player) loses \d+ life)",
        re.IGNORECASE,
    ),
    "lifegain": re.compile(
        r"whenever you gain (life|\d+ or more life)",
        re.IGNORECASE,
    ),
    "storm": re.compile(r"\bstorm\b", re.IGNORECASE),
}


def _match_any(oracle: str, patterns: list[re.Pattern]) -> bool:
    return any(p.search(oracle) for p in patterns)


def detect_archetype(cards: list[dict]) -> dict[str, Any]:
    """Detect deck archetype from card composition.

    Identical logic to ``services/ingest/import_utils.detect_archetype()``.
    Keep both in sync.

    Args:
        cards: list of dicts with keys ``oracle_text``, ``type_line``,
               ``cmc``, and optionally ``keywords``.

    Returns:
        Dict with ``archetype``, ``win_conditions``, ``avg_cmc``,
        ``role_counts``.
    """
    if not cards:
        return {
            "archetype":      "unknown",
            "win_conditions": [],
            "avg_cmc":        0.0,
            "role_counts":    {"ramp": 0, "draw": 0, "removal": 0, "tutor": 0},
        }

    creature_count        = 0
    instant_sorcery_count = 0
    non_land_cmcs: list[float] = []

    for card in cards:
        tl  = (card.get("type_line") or "").lower()
        cmc = float(card.get("cmc") or 0)
        if "creature" in tl:
            creature_count += 1
        if "instant" in tl or "sorcery" in tl:
            instant_sorcery_count += 1
        if "land" not in tl:
            non_land_cmcs.append(cmc)

    avg_cmc = round(sum(non_land_cmcs) / len(non_land_cmcs), 2) if non_land_cmcs else 0.0

    def _count(patterns: list[re.Pattern]) -> int:
        return sum(1 for c in cards if _match_any(c.get("oracle_text") or "", patterns))

    ramp_count        = _count(_RAMP_RES)
    draw_count        = _count(_DRAW_RES)
    removal_count     = _count(_REMOVAL_RES)
    tutor_count       = _count(_TUTOR_RES)
    token_count       = _count(_TOKEN_RES)
    recursion_count   = _count(_RECURSION_RES)
    anthem_count      = _count(_ANTHEM_RES)
    interaction_count = _count(_INTERACTION_RES)
    win_cond_count    = _count(_WIN_COND_ROLE_RES)
    stax_count        = _count(_STAX_RES)
    punisher_count    = _count(_PUNISHER_RES)
    spellslinger_payoffs = sum(
        1 for c in cards if _SPELLSLINGER_RE.search(c.get("oracle_text") or "")
    )

    role_counts = {
        "ramp":    ramp_count,
        "draw":    draw_count,
        "removal": removal_count,
        "tutor":   tutor_count,
    }

    win_conditions: list[str] = []
    for wc_name, wc_re in _WIN_CON_RES.items():
        if any(wc_re.search(c.get("oracle_text") or "") for c in cards):
            win_conditions.append(wc_name)
    if "infect" not in win_conditions:
        for c in cards:
            kws = c.get("keywords") or []
            if isinstance(kws, list) and any(
                k.lower() in ("infect", "toxic") for k in kws
            ):
                win_conditions.append("infect")
                break

    if stax_count >= 5:
        archetype = "stax"
    elif punisher_count >= 5:
        archetype = "punisher"
    elif recursion_count >= 5:
        archetype = "reanimator"
    elif token_count >= 8:
        archetype = "tokens"
    elif tutor_count >= 3 and (win_cond_count >= 2 or len(win_conditions) >= 1):
        archetype = "combo"
    elif instant_sorcery_count >= 10 and spellslinger_payoffs >= 2:
        archetype = "spellslinger"
    elif removal_count >= 18 and interaction_count >= 8:
        archetype = "control"
    elif creature_count >= 35 and avg_cmc <= 2.8 and anthem_count >= 3:
        archetype = "aggro"
    elif draw_count >= 10:
        archetype = "midrange"
    else:
        archetype = "midrange"

    return {
        "archetype":      archetype,
        "win_conditions": win_conditions,
        "avg_cmc":        avg_cmc,
        "role_counts":    role_counts,
    }


async def _fetch_card_details(db: AsyncSession, card_ids: list[str]) -> list[dict]:
    """Fetch card details needed for archetype detection via SQLAlchemy."""
    if not card_ids:
        return []
    rows = (
        await db.execute(
            text(
                "SELECT id::text, oracle_text, type_line, cmc, keywords "
                "FROM cards WHERE id::text = ANY(CAST(:ids AS text[]))"
            ),
            {"ids": card_ids},
        )
    ).fetchall()
    return [dict(r._mapping) for r in rows]


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


def _slugify(name: str) -> str:
    """Convert a card name to a lowercase hyphenated filename-style slug.

    Example: "Wilhelt, the Rotcleaver" → "wilhelt-the-rotcleaver"
    """
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug.strip())
    slug = re.sub(r"-+", "-", slug)
    return slug


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

    # Default deck name to slugified commander name when not supplied
    if not deck_name:
        deck_name = _slugify(cmd_name)

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

    # Detect archetype from card composition before inserting
    card_details = await _fetch_card_details(db, card_ids)
    arch_meta = detect_archetype(card_details)

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
            **arch_meta,
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
