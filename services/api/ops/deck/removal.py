"""Removal: anything that answers threats on the board.

Card type is irrelevant — Swords to Plowshares (instant), Ravenous
Chupacabra (creature), Merciless Eviction (sorcery), Detention Sphere
(enchantment), and Spine of Ish Sah (artifact) are all removal from
the deckbuilder's perspective.

Permanent removal (exile/destroy) is generally stronger than bounce
or tuck; board wipes serve a different role but are still 'removal'.
"""
from __future__ import annotations

import re

from .signals import DeckSignals

# Exile or destroy a permanent / creature / nonland
_HARD_REMOVAL_RE = re.compile(
    r"\bexile target\b"
    r"|\bdestroy target\b"
    r"|\bdestroys? all\b"
    r"|\bexile all\b"
    r"|\beach (player |opponent )?sacrifices\b",
    re.I,
)

# Bounce / tuck — softer, temporary answers
_SOFT_REMOVAL_RE = re.compile(
    r"\breturn target.{0,40}to (its owner.s hand|the bottom of|the top of)"
    r"|\bput target.{0,40}on the bottom"
    r"|\bcounters? target\b",
    re.I,
)

HARD_REMOVAL_BOOST = 1.3
SOFT_REMOVAL_BOOST = 1.1


def score_removal(
    scored: list[tuple[str, float]],
    oracle_texts: dict[str, str],
    signals: DeckSignals,  # noqa: ARG001 — reserved for future signal-gating
) -> list[tuple[str, float]]:
    """Boost cards that answer threats, regardless of card type.

    Hard removal (exile/destroy/wrath) receives a stronger boost than
    soft removal (bounce/counter).  No signal gate — every deck benefits
    from answers.
    """
    result = []
    for cid, sc in scored:
        ot = oracle_texts.get(cid, "")
        if _HARD_REMOVAL_RE.search(ot):
            sc = sc * HARD_REMOVAL_BOOST
        elif _SOFT_REMOVAL_RE.search(ot):
            sc = sc * SOFT_REMOVAL_BOOST
        result.append((cid, sc))
    return result
