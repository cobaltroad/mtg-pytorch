"""Value engine: anything that generates card advantage.

Card type is irrelevant — Rhystic Study (enchantment), Sylvan Library
(enchantment), Consecrated Sphinx (creature), Mystic Remora (enchantment),
Bonders' Enclave (land), and Harmonize (sorcery) are all card draw from
the deckbuilder's perspective.

Looting (draw then discard) is weaker than pure draw; wheels are separate
but still value; tutors are the strongest form of card selection.
"""
from __future__ import annotations

import re

from .signals import DeckSignals

# Pure card draw: "draw a card", "draw two cards", "draw X cards"
_DRAW_RE = re.compile(r"\bdraw (a|two|three|\w+ )?card(s)?\b", re.I)

# Looting / rummaging: draw then discard
_LOOT_RE = re.compile(r"\bdraw.{0,30}discard\b|\bdiscard.{0,30}draw\b", re.I)

# Tutors: search your library
_TUTOR_RE = re.compile(r"\bsearch your library\b", re.I)

DRAW_BOOST = 1.25
TUTOR_BOOST = 1.35
LOOT_BOOST = 1.1


def score_value_engine(
    scored: list[tuple[str, float]],
    oracle_texts: dict[str, str],
    signals: DeckSignals,  # noqa: ARG001 — reserved for future signal-gating
    tags: dict[str, list[str]] | None = None,
) -> list[tuple[str, float]]:
    """Boost cards that generate card advantage, regardless of card type.

    Tutors rank above pure draw, which ranks above looting.
    """
    result = []
    for cid, sc in scored:
        ot = oracle_texts.get(cid, "")
        if _TUTOR_RE.search(ot):
            sc = sc * TUTOR_BOOST
            if tags is not None:
                tags.setdefault(cid, []).append("value:tutor")
        elif _DRAW_RE.search(ot) and not _LOOT_RE.search(ot):
            sc = sc * DRAW_BOOST
            if tags is not None:
                tags.setdefault(cid, []).append("value:draw")
        elif _LOOT_RE.search(ot):
            sc = sc * LOOT_BOOST
            if tags is not None:
                tags.setdefault(cid, []).append("value:loot")
        result.append((cid, sc))
    return result
