"""Ramp: anything that produces mana.

Card type is irrelevant — Rampant Growth (sorcery), Arcane Signet
(artifact), Llanowar Elves (creature), and Cabal Coffers (land) are
all ramp from the deckbuilder's perspective.

Responsibilities:
  - Force-include Sol Ring / Arcane Signet regardless of model score
  - Boost mana-producing cards when "mana_producers" boost is active
  - Score non-basic lands by mana quality in three tiers:
      Tier 3 (SPECIFIC_DUAL_BOOST): specific commander-color duals, fetches,
              Shadowmoor filter lands — always preferred over any-color lands
      Tier 2 (ANY_COLOR_BOOST):     unrestricted {T}: Add one mana of any color
      Tier 1 (neutral):             single commander color, type-restricted match
      Tier 0 (COLORLESS_LAND_PENALTY): colorless-only, restricted non-match
"""
from __future__ import annotations

import re

from .signals import DeckSignals

# ── Mana production detection ─────────────────────────────────────────────────

# Activated mana ability: "{T}: Add …" or free "Add {" phrasing
_MANA_ADD_RE = re.compile(r"\{[tT]\}\s*:\s*[Aa]dd|\badd \{", re.I)

# Pure tap mana ability: cost is exactly {T}: with no other costs.
# Optional leading "(" handles shock land format: ({T}: Add {B} or {G}.)
_PURE_TAP_ADD_RE = re.compile(r"^\(?\{[Tt]\}\s*:\s*Add([^\n.]*)", re.M)

# Any colored mana symbol inside a captured clause
_COLOR_SYMBOL_RE = re.compile(r"\{([WUBRG])\}")

# Shadowmoor-style filter lands: {X/Y}, {T}: Add {X}{X}, {X}{Y}, or {Y}{Y}
_FILTER_LAND_RE = re.compile(r"\{[WUBRG]/[WUBRG]\},\s*\{[Tt]\}:\s*Add([^\n.]*)", re.M)

# Fetch lands: search for a basic land type and put it onto the battlefield
_FETCH_RE = re.compile(
    r"Search your library for (?:a |an )?(\w+)(?: or (\w+))? card[^.]*"
    r"put it onto the battlefield",
    re.I | re.S,
)
_BASIC_TYPE_TO_COLOR: dict[str, str] = {
    "swamp": "B", "forest": "G", "plains": "W", "island": "U", "mountain": "R",
}

# Type-restricted mana: "Spend this mana only to cast …"
_SPEND_ONLY_RE = re.compile(r"[Ss]pend this mana only to cast ([^.]+)", re.I)
_TYPE_WORD_RE = re.compile(r"\b[A-Z][a-z]+")

# Unconditionally enters tapped (conditional "unless …" forms excluded)
_UNCONDITIONAL_TAPPED_RE = re.compile(r"^This land enters tapped\.", re.M)

# Conditional self-sacrifice ("if you control no artifacts, sacrifice this land")
_CONDITIONAL_SACRIFICE_RE = re.compile(
    r"if you (?:control no|don't control a) (\w+),? sacrifice this land", re.I
)
_RELIABLE_PERMANENT_TYPES = frozenset({"creature", "creatures", "land", "lands"})

# Lands that never untap normally
_DOESNT_UNTAP_RE = re.compile(r"this land doesn't untap during your untap step", re.I)

# ── Constants ─────────────────────────────────────────────────────────────────

MANA_PRODUCER_BOOST    = 1.35
COLORLESS_LAND_PENALTY = 0.25
SPECIFIC_DUAL_BOOST    = 2.0   # shock/check/fetch/filter/bond/fast/slow lands
ANY_COLOR_BOOST        = 1.6   # City of Brass, Command Tower, Mana Confluence
TAPPED_LAND_PENALTY    = 0.8


# ── Helpers ───────────────────────────────────────────────────────────────────

def _type_restricted_mana_is_useful(
    oracle_text: str, deck_creature_types: frozenset[str]
) -> bool:
    """Return False if mana is restricted to types not present in the deck."""
    m = _SPEND_ONLY_RE.search(oracle_text)
    if not m:
        return True
    clause_types = {w.lower() for w in _TYPE_WORD_RE.findall(m.group(1))}
    return bool(clause_types & {t.lower() for t in deck_creature_types})


def _colors_produced(oracle_text: str) -> frozenset[str]:
    """Colors from pure {T}: Add abilities.  Returns {'ANY'} sentinel for any-color."""
    colors: set[str] = set()
    for m in _PURE_TAP_ADD_RE.finditer(oracle_text):
        clause = m.group(1)
        if "any color" in clause.lower():
            return frozenset({"ANY"})
        colors |= set(_COLOR_SYMBOL_RE.findall(clause))
    return frozenset(colors)


def _land_mana_tier(
    oracle_text: str,
    real_colors: frozenset[str],
    deck_creature_types: frozenset[str],
) -> int:
    """Return a mana quality tier for a non-basic land (0–3).

    3 — specific commander-color dual, fetch, or Shadowmoor filter land
    2 — unrestricted {T}: Add one mana of any color
    1 — single commander color, or type-restricted any-color with matching types
    0 — colorless-only or type-restricted with no matching types
    """
    if not _type_restricted_mana_is_useful(oracle_text, deck_creature_types):
        return 0

    # ── Fetch lands ───────────────────────────────────────────────────────────
    m = _FETCH_RE.search(oracle_text)
    if m:
        types = {t.lower() for t in m.groups() if t}
        fetch_colors = frozenset(
            _BASIC_TYPE_TO_COLOR[t] for t in types if t in _BASIC_TYPE_TO_COLOR
        )
        overlap = fetch_colors & real_colors
        if len(overlap) >= 2:
            return 3  # Verdant Catacombs for B/G — covers both colors
        if overlap:
            return 2  # Misty Rainforest for B/G — covers one

    # ── Shadowmoor filter lands ({X/Y}, {T}: Add) ────────────────────────────
    filter_colors: set[str] = set()
    for fm in _FILTER_LAND_RE.finditer(oracle_text):
        filter_colors |= set(_COLOR_SYMBOL_RE.findall(fm.group(1)))
    if len(filter_colors & real_colors) >= 2:
        return 3
    if filter_colors & real_colors:
        return 1  # filter land that only covers one commander color (e.g. Flooded Grove in B/G)

    # ── Pure-tap specific or any-color ────────────────────────────────────────
    produced = _colors_produced(oracle_text)
    if "ANY" in produced:
        # Type-restricted any-color is useful but not as good as a real dual
        if _SPEND_ONLY_RE.search(oracle_text):
            return 1
        return 2

    specific = produced & real_colors
    if len(specific) >= 2:
        return 3
    if specific:
        return 1
    return 0


def produces_commander_color(
    oracle_text: str,
    real_colors: frozenset[str],
    deck_creature_types: frozenset[str] = frozenset(),
) -> bool:
    """True if this card can produce any of the commander's colors."""
    return _land_mana_tier(oracle_text, real_colors, deck_creature_types) >= 1


# ── Scorers ───────────────────────────────────────────────────────────────────

def score_mana_producers(
    scored: list[tuple[str, float]],
    oracle_texts: dict[str, str],
    signals: DeckSignals,
    tags: dict[str, list[str]] | None = None,
) -> list[tuple[str, float]]:
    """Boost any card with a mana-producing ability when the commander needs them."""
    if "mana_producers" not in signals.active_boosts:
        return scored
    result = []
    for cid, sc in scored:
        if _MANA_ADD_RE.search(oracle_texts.get(cid, "")):
            sc = sc * MANA_PRODUCER_BOOST
            if tags is not None:
                tags.setdefault(cid, []).append("ramp:mana_producer")
        result.append((cid, sc))
    return result


def score_land_mana_quality(
    nonbasic_scored: list[tuple[str, float]],
    oracle_texts: dict[str, str],
    signals: DeckSignals,
    tags: dict[str, list[str]] | None = None,
    deck_creature_types: frozenset[str] = frozenset(),
) -> list[tuple[str, float]]:
    """Score non-basic lands by mana quality tier, then apply usage penalties.

    Tier 3 lands (specific duals, fetches, filter lands) always outrank
    tier 2 (any-color), which outranks tier 1 (single color / restricted),
    which outranks tier 0 (colorless / useless).  Penalties for entering
    tapped, conditional sacrifice, and not untapping are applied on top.
    """
    if not signals.real_colors:
        return nonbasic_scored
    result = []
    for cid, sc in nonbasic_scored:
        ot = oracle_texts.get(cid, "")
        tier = _land_mana_tier(ot, signals.real_colors, deck_creature_types)

        if tier >= 3:
            sc = sc * SPECIFIC_DUAL_BOOST
            if tags is not None:
                tags.setdefault(cid, []).append("land:specific_dual_boost")
        elif tier == 2:
            sc = sc * ANY_COLOR_BOOST
            if tags is not None:
                tags.setdefault(cid, []).append("land:dual_boost")
        elif tier == 0:
            sc = sc * COLORLESS_LAND_PENALTY
            if tags is not None:
                tags.setdefault(cid, []).append("land:colorless_penalty")
        # tier == 1: neutral — no multiplier

        # Usage penalties applied after the tier multiplier
        if _UNCONDITIONAL_TAPPED_RE.search(ot):
            sc = sc * TAPPED_LAND_PENALTY
            if tags is not None:
                tags.setdefault(cid, []).append("land:tapped_penalty")
        m = _CONDITIONAL_SACRIFICE_RE.search(ot)
        if m and m.group(1).lower() not in _RELIABLE_PERMANENT_TYPES:
            sc = sc * COLORLESS_LAND_PENALTY
            if tags is not None:
                tags.setdefault(cid, []).append("land:colorless_penalty")
        if _DOESNT_UNTAP_RE.search(ot):
            sc = sc * COLORLESS_LAND_PENALTY
            if tags is not None:
                tags.setdefault(cid, []).append("land:colorless_penalty")

        result.append((cid, sc))
    return result


def select_ramp(
    spell_scored: list[tuple[str, float]],
    ramp_ids: frozenset[str],
    guaranteed_ramp: dict[str, str],
    ramp_target: int,
    tags: dict[str, list[str]] | None = None,
) -> tuple[list[tuple[str, float]], set[str]]:
    """Select ramp cards, force-including Sol Ring and Arcane Signet first."""
    score_lookup = {cid: sc for cid, sc in spell_scored}

    selected: list[tuple[str, float]] = []
    for cid in guaranteed_ramp.values():
        selected.append((cid, score_lookup.get(cid, 0.0)))
        if tags is not None:
            tags.setdefault(cid, []).append("ramp:guaranteed")

    preselected = {cid for cid, _ in selected}
    candidates = [
        (cid, sc) for cid, sc in spell_scored
        if cid in ramp_ids and cid not in preselected
    ]
    selected.extend(candidates[:ramp_target - len(selected)])
    return selected, {cid for cid, _ in selected}
