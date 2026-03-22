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

# Colored symbol inside an Add clause ("Add {G}", "Add {B}{G}")
_ADD_COLOR_RE = re.compile(r"[Aa]dd[^.\n]{0,60}\{([WUBRG])\}")

# "Any color" phrasing: City of Brass, Mana Confluence, Command Tower, etc.
_ADD_ANY_COLOR_RE = re.compile(r"[Aa]dd[^.\n]{0,40}any color", re.I)

MANA_PRODUCER_BOOST = 1.35
COLORLESS_LAND_PENALTY = 0.25


def produces_commander_color(oracle_text: str, real_colors: frozenset[str]) -> bool:
    """True if the text's mana production includes any of the commander's colors."""
    if _ADD_ANY_COLOR_RE.search(oracle_text):
        return True
    return any(
        m.group(1) in real_colors
        for m in _ADD_COLOR_RE.finditer(oracle_text)
    )


def score_mana_producers(
    scored: list[tuple[str, float]],
    oracle_texts: dict[str, str],
    signals: DeckSignals,
) -> list[tuple[str, float]]:
    """Boost any card with a mana-producing ability when the commander needs them.

    Applies equally to mana dorks, rocks, and ritual-style effects.
    Only active when "mana_producers" is in active_boosts.
    """
    if "mana_producers" not in signals.active_boosts:
        return scored
    return [
        (cid, sc * MANA_PRODUCER_BOOST if _MANA_ADD_RE.search(oracle_texts.get(cid, "")) else sc)
        for cid, sc in scored
    ]


def score_land_mana_quality(
    nonbasic_scored: list[tuple[str, float]],
    oracle_texts: dict[str, str],
    signals: DeckSignals,
) -> list[tuple[str, float]]:
    """Penalise non-basic lands that produce no mana in the commander's colors.

    Urza's Tower, Tomb of the Spirit Dragon, Blinkmoth Nexus, etc. sink
    to the bottom of the land pool so colored duals and fetch lands rank
    first.  No-op for colorless commanders.
    """
    if not signals.real_colors:
        return nonbasic_scored
    return [
        (
            cid,
            sc if produces_commander_color(oracle_texts.get(cid, ""), signals.real_colors)
            else sc * COLORLESS_LAND_PENALTY,
        )
        for cid, sc in nonbasic_scored
    ]


def select_ramp(
    spell_scored: list[tuple[str, float]],
    ramp_ids: frozenset[str],
    guaranteed_ramp: dict[str, str],
    ramp_target: int,
) -> tuple[list[tuple[str, float]], set[str]]:
    """Select ramp cards, force-including Sol Ring and Arcane Signet first.

    Guaranteed cards are included even if they were excluded from model
    scoring (e.g. because they landed in the proxy context seed).
    """
    score_lookup = {cid: sc for cid, sc in spell_scored}

    selected: list[tuple[str, float]] = [
        (cid, score_lookup.get(cid, 0.0))
        for cid in guaranteed_ramp.values()
    ]
    preselected = {cid for cid, _ in selected}

    candidates = [
        (cid, sc) for cid, sc in spell_scored
        if cid in ramp_ids and cid not in preselected
    ]
    selected.extend(candidates[:ramp_target - len(selected)])
    return selected, {cid for cid, _ in selected}
