"""Card facts — structured mana profile + land classification (Layer 1).

Pure functions over the card fields already stored in the ``cards`` table
(``mana_cost``, ``type_line``, ``oracle_text``, ``produced_mana``, ``faces``).
Everything downstream — quota derivation, Karsten castability math, the mana
base solver — reads these facts instead of re-parsing oracle text.

Mana symbol handling
--------------------
{W}{U}{B}{R}{G}   strict colored pips → ``ManaProfile.pips``
{C}               strict colorless pip (Kozilek) → ``pips["C"]``
{S}               snow pip (Icehide Golem) → ``pips["S"]``
{2} {15} …        generic → ``ManaProfile.generic``
{X} {Y} {Z}       variable → ``ManaProfile.has_x``
{W/U}             hybrid → one ``hybrid`` entry ["W", "U"]
{2/W}             monocolor hybrid → ["2", "W"]
{B/P} {G/W/P}     phyrexian → ["B", "P"] / ["G", "W", "P"]; "P" = pay 2 life

For castability math: a color's *minimum* source requirement comes from
``pips``; ``hybrid`` entries relax it (any listed color, generic mana, or
life can pay instead).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

COLORS = "WUBRG"
_STRICT_PIPS = frozenset("WUBRGCS")
_VARIABLE = frozenset("XYZ")
_SYMBOL_RE = re.compile(r"\{([^{}]+)\}")

_LAND_RE = re.compile(r"\bLand\b")
_BASIC_RE = re.compile(r"\bBasic\b")
# Matches both current ("This land enters tapped") and pre-2024
# ("Krosan Verge enters the battlefield tapped") templating.
_TAPPED_RE = re.compile(r"enters (?:the battlefield )?tapped", re.IGNORECASE)
# Sac-to-search: true fetches name basic land *types* without the word
# "land" ("an Island or Swamp card"), so match types as well.
_FETCH_RE = re.compile(
    r"sacrifice [^:]*:\s*search your library for "
    r"[^.]*(?:land|plains|island|swamp|mountain|forest)",
    re.IGNORECASE,
)


@dataclass
class ManaProfile:
    generic: int = 0
    has_x: bool = False
    pips: dict[str, int] = field(default_factory=dict)
    hybrid: list[list[str]] = field(default_factory=list)


@dataclass
class LandFacts:
    is_land: bool = False
    is_basic: bool = False
    land_colors: list[str] = field(default_factory=list)
    etb_tapped: str | None = None  # 'always' | 'conditional' | 'untapped'; None for nonlands
    is_fetch: bool = False


@dataclass
class CardFacts:
    mana: ManaProfile
    land: LandFacts
    is_mdfc_land: bool = False


def parse_mana_cost(mana_cost: str | None) -> ManaProfile:
    """Parse a mana cost string like ``{1}{B/P}{B/P}`` into a ManaProfile.

    Cards with no mana cost (lands, suspend-only spells) return an empty
    profile.  Unrecognised symbols (un-set half mana etc.) are ignored.
    """
    profile = ManaProfile()
    if not mana_cost:
        return profile
    for sym in _SYMBOL_RE.findall(mana_cost):
        sym = sym.upper()
        if sym.isdigit():
            profile.generic += int(sym)
        elif sym in _VARIABLE:
            profile.has_x = True
        elif "/" in sym:
            profile.hybrid.append(sym.split("/"))
        elif sym in _STRICT_PIPS:
            profile.pips[sym] = profile.pips.get(sym, 0) + 1
    return profile


def _tapped_class(oracle_text: str) -> str:
    """Classify a land's ETB-tapped behaviour from its oracle text.

    'always'      — unconditional ("This land enters tapped.")
    'conditional' — escape hatch exists (shock/check/fast/tango lands:
                    "unless…", "you may pay 2 life. If you don't, it
                    enters tapped").
    'untapped'    — no self-tapping clause.  Clauses about *searched*
                    cards ("put it onto the battlefield tapped") use
                    different wording and do not match.
    """
    match = _TAPPED_RE.search(oracle_text)
    if not match:
        return "untapped"
    # Judge only the sentence containing the tapped clause.
    start = oracle_text.rfind(".", 0, match.start()) + 1
    end = oracle_text.find(".", match.end())
    sentence = oracle_text[start : end if end != -1 else len(oracle_text)].lower()
    if "unless" in sentence or "if you don't" in sentence or "you may" in sentence:
        return "conditional"
    return "always"


def classify_land(
    type_line: str | None,
    oracle_text: str | None,
    produced_mana: list[str] | None,
) -> LandFacts:
    """Classify a card's land behaviour from its front-face fields."""
    tl = type_line or ""
    text = oracle_text or ""
    facts = LandFacts(is_land=bool(_LAND_RE.search(tl)))
    if not facts.is_land:
        return facts
    facts.is_basic = bool(_BASIC_RE.search(tl))
    facts.land_colors = [c for c in (produced_mana or []) if c in COLORS + "C"]
    facts.etb_tapped = _tapped_class(text)
    facts.is_fetch = bool(_FETCH_RE.search(text))
    return facts


def is_mdfc_land(faces: list[dict] | None) -> bool:
    """True for modal double-faced cards with at least one land face.

    Accepts MTGJSON atomic faces (``layout``/``type``) and Scryfall
    card_faces (``type_line``).  Transform DFCs that flip into lands
    (Growing Rites of Itlimoc) are *not* MDFCs — they can't be played as
    a land, so they don't count toward the mana base.
    """
    if not faces or len(faces) < 2:
        return False
    if (faces[0].get("layout") or "").lower() != "modal_dfc":
        return False
    return any(
        _LAND_RE.search(f.get("type") or f.get("type_line") or "") for f in faces
    )


def compute_card_facts(
    mana_cost: str | None,
    type_line: str | None,
    oracle_text: str | None,
    produced_mana: list[str] | None,
    faces: list[dict] | None = None,
) -> CardFacts:
    """Compute all Layer-1 facts for one card."""
    mana = parse_mana_cost(mana_cost)
    land = classify_land(type_line, oracle_text, produced_mana)
    mdfc = is_mdfc_land(faces)
    # A spell-front MDFC's land face still produces mana; MTGJSON mirrors
    # producedMana onto the front face, so record the colors even though
    # the front face is not a land.
    if mdfc and not land.is_land:
        land.land_colors = [c for c in (produced_mana or []) if c in COLORS + "C"]
    return CardFacts(mana=mana, land=land, is_mdfc_land=mdfc)
