"""Land oracle text augmentation for embedding quality.

Before computing sentence-transformer embeddings, Land cards receive structured
mana-quality tags prepended to their oracle text.  This teaches the model that
Verdant Catacombs, Overgrown Tomb, and Woodland Cemetery belong in the same deck
— something their sparse oracle text cannot communicate on its own.

Tag format
----------
  [FETCH_LAND:BG]       fetch land covering B and G
  [FILTER_LAND:BG]      Shadowmoor/Lorwyn filter land
  [DUAL_LAND:BG]        specific dual (shock/check/bond/fast/slow/pain/bounce/tri)
  [SINGLE_COLOR_LAND:B] produces exactly one non-colorless color
  [ANY_COLOR_LAND]      unrestricted any-color (Command Tower, Mana Confluence)
  [SHOCK_LAND]          you may pay 2 life cycle
  [CHECK_LAND]          enters tapped unless you control a <type> or a <type>
  [BOND_LAND]           enters tapped unless you have two or more opponents
  [FAST_LAND]           enters tapped unless you control two or fewer other lands
  [SLOW_LAND]           enters tapped unless you control two or more basic lands
  [PAIN_LAND]           {T}: Add {C}; {T}: Add color, deals 1 damage
  [SURVEIL_LAND]        enters tapped + surveil 1
  [GAIN_LAND]           enters tapped + you gain 1 life
  [BOUNCE_LAND]         enters tapped + return a land to hand
  [ENTERS_TAPPED]       unconditionally enters tapped
  [CONDITIONAL_SACRIFICE]  sacrificed if a type-based condition fails
  [DOESNT_UNTAP]        doesn't untap during your untap step
  [TYPE_RESTRICTED]     mana can only be spent on specific card types

Colors are always in WUBRG order.
"""

from __future__ import annotations

import re

# ── Color helpers ──────────────────────────────────────────────────────────────

_COLOR_ORDER = "WUBRG"


def _sort_colors(colors: set[str]) -> str:
    return "".join(c for c in _COLOR_ORDER if c in colors)


# ── Detection patterns ─────────────────────────────────────────────────────────

# Pure {T}: Add abilities.  Optional "(" handles shock land format: ({T}: Add …)
_PURE_TAP_ADD_RE = re.compile(r"^\(?\{[Tt]\}\s*:\s*Add([^\n.]*)", re.M)

# Any color symbol in a captured clause
_COLOR_SYMBOL_RE = re.compile(r"\{([WUBRG])\}")

# Hybrid mana symbols in activation cost (filter lands: {B/G}, {T}: Add …)
_HYBRID_COST_RE = re.compile(r"\{([WUBRG])/([WUBRG])\}")

# Fetch lands: search for a basic land type card, put it onto the battlefield
_FETCH_RE = re.compile(
    r"Search your library for (?:a |an )?(\w+)(?: or (\w+))? card[^.]*"
    r"put it onto the battlefield",
    re.I | re.S,
)
_BASIC_TYPE_TO_COLOR: dict[str, str] = {
    "swamp": "B", "forest": "G", "plains": "W", "island": "U", "mountain": "R",
}

# Shadowmoor/Lorwyn filter lands: {X/Y}, {T}: Add …
_FILTER_LAND_RE = re.compile(
    r"\{[WUBRG]/[WUBRG]\},\s*\{[Tt]\}:\s*Add([^\n.]*)", re.M
)

# ── Cycle patterns ─────────────────────────────────────────────────────────────

_SHOCK_LAND_RE    = re.compile(r"you may pay 2 life\. If you don't, it enters tapped", re.I)
_CHECK_LAND_RE    = re.compile(r"enters tapped unless you control a \w+ or a \w+", re.I)
_BOND_LAND_RE     = re.compile(r"enters tapped unless you have two or more opponents", re.I)
_FAST_LAND_RE     = re.compile(r"enters tapped unless you control two or fewer other lands", re.I)
_SLOW_LAND_RE     = re.compile(r"enters tapped unless you control two or more basic lands", re.I)
_PAIN_LAND_RE     = re.compile(r"\{T\}:\s*Add\s*\{C\}.*\{T\}:\s*Add[^\n]*deals 1 damage", re.I | re.S)
_SURVEIL_LAND_RE  = re.compile(r"\bsurveil 1\b", re.I)
_GAIN_LAND_RE     = re.compile(r"\byou gain 1 life\b", re.I)
_BOUNCE_LAND_RE   = re.compile(r"return a land you control to its owner's hand", re.I)

# ── Penalty patterns ───────────────────────────────────────────────────────────

# Unconditionally enters tapped: covers "[Name] enters tapped." and
# "This land enters tapped." — the period immediately after "tapped" signals
# no trailing "unless …" clause.  Shock lands have "If you don't, it enters
# tapped." which is conditional; the look-behind for "if you don't" excludes it.
_UNCONDITIONAL_TAPPED_RE = re.compile(r"\benters tapped\.", re.I)
_TAPPED_CONDITIONAL_RE   = re.compile(r"(unless|if you don't)", re.I)

# Any-color mana ability with possible additional costs ({T}, Pay 1 life: Add …)
_ANY_COLOR_MANA_RE = re.compile(r"\{[Tt]\}[^:]*:\s*Add\b[^\n]*\bany color\b", re.I)

# Conditional sacrifice ("if you control no artifacts, sacrifice this land")
_CONDITIONAL_SACRIFICE_RE = re.compile(
    r"if you (?:control no|don't control a) (\w+),? sacrifice this land", re.I
)
_RELIABLE_PERMANENT_TYPES = frozenset({"creature", "creatures", "land", "lands"})

# Doesn't untap normally
_DOESNT_UNTAP_RE = re.compile(r"this land doesn't untap during your untap step", re.I)

# Type-restricted mana ("Spend this mana only to cast …")
_SPEND_ONLY_RE = re.compile(r"[Ss]pend this mana only to cast", re.I)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _tap_add_colors(oracle_text: str) -> frozenset[str]:
    """Colors from pure {T}: Add abilities.  Returns {'ANY'} sentinel for any-color."""
    colors: set[str] = set()
    for m in _PURE_TAP_ADD_RE.finditer(oracle_text):
        clause = m.group(1)
        if "any color" in clause.lower():
            return frozenset({"ANY"})
        colors |= set(_COLOR_SYMBOL_RE.findall(clause))
    return frozenset(colors)


def _filter_land_colors(oracle_text: str) -> frozenset[str]:
    """Colors from Shadowmoor-style filter land activations."""
    colors: set[str] = set()
    for m in _FILTER_LAND_RE.finditer(oracle_text):
        colors |= set(_COLOR_SYMBOL_RE.findall(m.group(1)))
    # also grab the hybrid symbols in the activation cost ({B/G}, {T}: …)
    for m in _HYBRID_COST_RE.finditer(oracle_text):
        colors.add(m.group(1))
        colors.add(m.group(2))
    return frozenset(colors)


# ── Public API ─────────────────────────────────────────────────────────────────

def build_land_tags(oracle_text: str) -> list[str]:
    """Return a list of structured mana-quality tags for a land card.

    Tags describe what the land *is* (fetch, filter, dual, etc.) and what
    penalties apply (enters tapped, conditional sacrifice, etc.).  Multiple
    tags may apply to the same land (e.g. ``["DUAL_LAND:BG", "SHOCK_LAND"]``).
    """
    if not oracle_text:
        return []

    tags: list[str] = []

    # ── Primary mana-production tag ────────────────────────────────────────────
    fetch_m = _FETCH_RE.search(oracle_text)
    if fetch_m:
        types = {t.lower() for t in fetch_m.groups() if t}
        colors = {_BASIC_TYPE_TO_COLOR[t] for t in types if t in _BASIC_TYPE_TO_COLOR}
        tags.append(f"FETCH_LAND:{_sort_colors(colors)}" if colors else "FETCH_LAND")

    elif _FILTER_LAND_RE.search(oracle_text):
        color_str = _sort_colors(set(_filter_land_colors(oracle_text)))
        tags.append(f"FILTER_LAND:{color_str}" if color_str else "FILTER_LAND")

    else:
        # Check for any-color first (handles additional-cost taps like Mana Confluence)
        if _ANY_COLOR_MANA_RE.search(oracle_text):
            tags.append("ANY_COLOR_LAND")
        else:
            produced = _tap_add_colors(oracle_text)
            if "ANY" in produced:
                tags.append("ANY_COLOR_LAND")
            elif len(produced) >= 2:
                tags.append(f"DUAL_LAND:{_sort_colors(set(produced))}")
            elif len(produced) == 1:
                tags.append(f"SINGLE_COLOR_LAND:{next(iter(produced))}")

    # ── Cycle tags (at most one; ordered by specificity) ──────────────────────
    if _SHOCK_LAND_RE.search(oracle_text):
        tags.append("SHOCK_LAND")
    elif _CHECK_LAND_RE.search(oracle_text):
        tags.append("CHECK_LAND")
    elif _BOND_LAND_RE.search(oracle_text):
        tags.append("BOND_LAND")
    elif _FAST_LAND_RE.search(oracle_text):
        tags.append("FAST_LAND")
    elif _SLOW_LAND_RE.search(oracle_text):
        tags.append("SLOW_LAND")

    if _PAIN_LAND_RE.search(oracle_text):
        tags.append("PAIN_LAND")
    if _SURVEIL_LAND_RE.search(oracle_text):
        tags.append("SURVEIL_LAND")
    if _GAIN_LAND_RE.search(oracle_text):
        tags.append("GAIN_LAND")
    if _BOUNCE_LAND_RE.search(oracle_text):
        tags.append("BOUNCE_LAND")

    # ── Penalty tags ──────────────────────────────────────────────────────────
    for tapped_m in _UNCONDITIONAL_TAPPED_RE.finditer(oracle_text):
        # Check the sentence containing the match for conditional modifiers
        # ("unless …" or "If you don't …" as in shock lands).
        sentence_start = oracle_text.rfind("\n", 0, tapped_m.start()) + 1
        sentence = oracle_text[sentence_start : tapped_m.end() + 100]
        if not _TAPPED_CONDITIONAL_RE.search(sentence):
            tags.append("ENTERS_TAPPED")
            break

    cond_m = _CONDITIONAL_SACRIFICE_RE.search(oracle_text)
    if cond_m and cond_m.group(1).lower() not in _RELIABLE_PERMANENT_TYPES:
        tags.append("CONDITIONAL_SACRIFICE")

    if _DOESNT_UNTAP_RE.search(oracle_text):
        tags.append("DOESNT_UNTAP")

    if _SPEND_ONLY_RE.search(oracle_text):
        tags.append("TYPE_RESTRICTED")

    return tags


def annotate_land_oracle(oracle_text: str) -> str:
    """Prepend structured mana-quality tags to a land card's oracle text.

    Returns the oracle text unchanged if no tags apply (e.g. a basic land
    with no special abilities).

    Example::

        >>> annotate_land_oracle("({T}: Add {B} or {G}.)\\nAs Overgrown Tomb enters, "
        ...                       "you may pay 2 life. If you don't, it enters tapped.")
        '[DUAL_LAND:BG] [SHOCK_LAND] ({T}: Add {B} or {G}.) ...'
    """
    tags = build_land_tags(oracle_text)
    if not tags:
        return oracle_text
    prefix = " ".join(f"[{t}]" for t in tags)
    return f"{prefix} {oracle_text}"
