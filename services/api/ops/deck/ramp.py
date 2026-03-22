"""Ramp: anything that produces mana.

Card type is irrelevant — Rampant Growth (sorcery), Arcane Signet
(artifact), Llanowar Elves (creature), and Cabal Coffers (land) are
all ramp from the deckbuilder's perspective.

Responsibilities:
  - Force-include Sol Ring / Arcane Signet regardless of model score
  - Boost mana-producing cards when "mana_producers" boost is active
  - Penalise non-basic lands that produce no mana in the commander's colors
"""
from __future__ import annotations

import re

from .signals import DeckSignals

# Activated mana ability: "{T}: Add …" or free "Add {" phrasing
_MANA_ADD_RE = re.compile(r"\{[tT]\}\s*:\s*[Aa]dd|\badd \{", re.I)

# Pure tap mana ability: cost is exactly {T}: with no other costs.
# Captures everything after "Add" up to end of sentence/line.
# Excludes abilities like "{T}, Pay {E}:", "{1}, {T}:", "{T}, Sacrifice ...:".
_PURE_TAP_ADD_RE = re.compile(r"^\{[Tt]\}\s*:\s*Add([^\n.]*)", re.M)

# Any colored mana symbol — applied inside a captured Add clause
_COLOR_SYMBOL_RE = re.compile(r"\{([WUBRG])\}")

# "Any color" phrasing: City of Brass, Mana Confluence, Command Tower, etc.
_ADD_ANY_COLOR_RE = re.compile(r"[Aa]dd[^.\n]{0,40}any color", re.I)

# Type-restricted mana — detect presence and capture everything up to next period
_SPEND_ONLY_RE = re.compile(r"[Ss]pend this mana only to cast ([^.]+)", re.I)

# Capitalised words likely to be creature type names within a restriction clause
_TYPE_WORD_RE = re.compile(r"\b[A-Z][a-z]+")

# Unconditionally enters tapped — "unless …" and "If you don't …" variants are excluded
_UNCONDITIONAL_TAPPED_RE = re.compile(r"^This land enters tapped\.", re.M)

MANA_PRODUCER_BOOST = 1.35
COLORLESS_LAND_PENALTY = 0.25
DUAL_LAND_BOOST = 1.6
TAPPED_LAND_PENALTY = 0.8


def _type_restricted_mana_is_useful(
    oracle_text: str, deck_creature_types: frozenset[str]
) -> bool:
    """Return False if mana is restricted to creature types not present in the deck.

    Captures the full restriction clause to handle multi-word types (Time Lord),
    adjective-qualified types (Dragon creature), and comma-separated lists
    (Cleric, Rogue, Warrior, or Wizard).
    """
    m = _SPEND_ONLY_RE.search(oracle_text)
    if not m:
        return True
    clause_types = {w.lower() for w in _TYPE_WORD_RE.findall(m.group(1))}
    deck_lower = {t.lower() for t in deck_creature_types}
    return bool(clause_types & deck_lower)


def _colors_produced(oracle_text: str) -> frozenset[str]:
    """Return colored mana symbols from pure-tap abilities only ({T}: Add ...).

    Abilities with additional costs ({1}, {E}, Sacrifice, Pay life, etc.)
    are excluded — those are conditional and don't count as reliable mana fixing.
    """
    colors: set[str] = set()
    for m in _PURE_TAP_ADD_RE.finditer(oracle_text):
        clause = m.group(1)
        if "any color" in clause.lower():
            return frozenset({"ANY"})  # sentinel: caller handles this
        colors |= set(_COLOR_SYMBOL_RE.findall(clause))
    return frozenset(colors)


def produces_commander_color(
    oracle_text: str,
    real_colors: frozenset[str],
    deck_creature_types: frozenset[str] = frozenset(),
) -> bool:
    """True if a pure-tap ability produces any of the commander's colors."""
    if not _type_restricted_mana_is_useful(oracle_text, deck_creature_types):
        return False
    produced = _colors_produced(oracle_text)
    if "ANY" in produced:
        return bool(real_colors)
    return bool(produced & real_colors)


def score_mana_producers(
    scored: list[tuple[str, float]],
    oracle_texts: dict[str, str],
    signals: DeckSignals,
    tags: dict[str, list[str]] | None = None,
) -> list[tuple[str, float]]:
    """Boost any card with a mana-producing ability when the commander needs them.

    Applies equally to mana dorks, rocks, and ritual-style effects.
    Only active when "mana_producers" is in active_boosts.
    """
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


def _count_commander_colors_produced(
    oracle_text: str,
    real_colors: frozenset[str],
    deck_creature_types: frozenset[str] = frozenset(),
) -> int:
    """Count how many distinct commander colors this land can produce.

    Returns 0 if the land's colored mana is restricted to creature types
    not present in the deck.
    """
    if not _type_restricted_mana_is_useful(oracle_text, deck_creature_types):
        return 0
    produced = _colors_produced(oracle_text)
    if "ANY" in produced:
        # Type-restricted any-color (Base Camp, Cavern of Souls, etc.) is useful
        # but narrower than an unrestricted dual — counts as 1, not all colors.
        if _SPEND_ONLY_RE.search(oracle_text):
            return 1
        return len(real_colors)
    return len(produced & real_colors)


def score_land_mana_quality(
    nonbasic_scored: list[tuple[str, float]],
    oracle_texts: dict[str, str],
    signals: DeckSignals,
    tags: dict[str, list[str]] | None = None,
    deck_creature_types: frozenset[str] = frozenset(),
) -> list[tuple[str, float]]:
    """Boost dual lands; penalise non-basic lands that produce no commander colors.

    Lands producing ≥2 commander colors (shocks, checks, temples, etc.) get a
    boost so they outrank utility lands.  Colorless-only lands (Urza's Tower,
    Blinkmoth Nexus, etc.) are penalised.  No-op for colorless commanders.

    deck_creature_types: all creature subtypes in the top spell candidates,
    used to detect type-restricted lands like Turtle Lair that are useless
    when their restricted type isn't in the deck.
    """
    if not signals.real_colors:
        return nonbasic_scored
    result = []
    for cid, sc in nonbasic_scored:
        colors_produced = _count_commander_colors_produced(
            oracle_texts.get(cid, ""), signals.real_colors, deck_creature_types
        )
        ot = oracle_texts.get(cid, "")
        if colors_produced >= 2:
            sc = sc * DUAL_LAND_BOOST
            if tags is not None:
                tags.setdefault(cid, []).append("land:dual_boost")
        elif colors_produced == 0:
            sc = sc * COLORLESS_LAND_PENALTY
            if tags is not None:
                tags.setdefault(cid, []).append("land:colorless_penalty")
        if _UNCONDITIONAL_TAPPED_RE.search(ot):
            sc = sc * TAPPED_LAND_PENALTY
            if tags is not None:
                tags.setdefault(cid, []).append("land:tapped_penalty")
        result.append((cid, sc))
    return result


def select_ramp(
    spell_scored: list[tuple[str, float]],
    ramp_ids: frozenset[str],
    guaranteed_ramp: dict[str, str],
    ramp_target: int,
    tags: dict[str, list[str]] | None = None,
) -> tuple[list[tuple[str, float]], set[str]]:
    """Select ramp cards, force-including Sol Ring and Arcane Signet first.

    Guaranteed cards are included even if they were excluded from model
    scoring (e.g. because they landed in the proxy context seed).
    """
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
