"""Evasion and combat enablement: anything that lets your creatures connect.

Card type is irrelevant — Rogue's Passage (land), Whispersilk Cloak
(artifact), Odric, Lunarch Marshal (creature), and Temur Battle Rage
(instant) are all evasion enablers from the deckbuilder's perspective.

Access Tunnel specifically grants evasion to small creatures (power ≤ 1),
which is particularly strong in tribal decks where most creatures are 1/1
or 1/2 — receives an extra multiplier when the commander has a tribal type.
"""
from __future__ import annotations

import re

from .signals import DeckSignals

# Grants unblockability to any creature
_EVASION_RE = re.compile(
    r"can't be blocked"
    r"|becomes? unblockable"
    r"|is unblockable",
    re.I,
)

# Grants evasion specifically to small creatures (Access Tunnel pattern)
_SMALL_EVASION_RE = re.compile(
    r"power [12] or greater can't block"
    r"|can't be blocked by creatures with power [12] or greater"
    r"|only creatures? with power [12] or less can block",
    re.I,
)

EVASION_BOOST = 1.8
SMALL_EVASION_TRIBAL_BONUS = 1.5


def score_evasion_enablers(
    scored: list[tuple[str, float]],
    oracle_texts: dict[str, str],
    signals: DeckSignals,
) -> list[tuple[str, float]]:
    """Boost any card that grants evasion when the deck wants to attack.

    Applied to lands, artifacts, creatures, instants — whatever grants
    the effect.  Access Tunnel gets an extra boost in tribal decks where
    the creatures are typically small.
    """
    if not signals.wants_attack:
        return scored

    result = []
    for cid, sc in scored:
        ot = oracle_texts.get(cid, "")
        if _EVASION_RE.search(ot):
            multiplier = EVASION_BOOST
            if signals.tribal_types and _SMALL_EVASION_RE.search(ot):
                multiplier *= SMALL_EVASION_TRIBAL_BONUS
            sc = sc * multiplier
        result.append((cid, sc))
    return result
