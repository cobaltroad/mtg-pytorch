"""Regression tests for ORACLE_PATTERNS detection (issue #133 onward).

Pure: runs _detect against hardcoded oracle texts — no DB.  Add a case here
whenever a decompose pattern is added or widened, including a negative case
showing what must NOT fire.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from stages.decompose import _detect  # noqa: E402


def keys(oracle_text: str, type_line: str = "Legendary Creature") -> set[str]:
    return {k for k, _label, _phrase in _detect(oracle_text, type_line)}


# ── graveyard_payoff: both word orders (issue #133) ──────────────────────────

MULDROTHA = (
    "During each of your turns, you may play a land and cast a permanent "
    "spell of each permanent type from your graveyard."
)
KARADOR = (
    "This spell costs {1} less to cast for each creature card in your graveyard.\n"
    "Once during each of your turns, you may cast a creature spell from your graveyard."
)
GISA_AND_GERALF = "During each of your turns, you may cast Zombie spells from your graveyard."
LURRUS_STYLE = (
    "During each of your turns, you may cast one permanent spell with mana "
    "value 2 or less from your graveyard."
)


@pytest.mark.parametrize(
    "text", [MULDROTHA, KARADOR, GISA_AND_GERALF, LURRUS_STYLE],
    ids=["muldrotha", "karador", "gisa-geralf", "lurrus-style"],
)
def test_cast_from_graveyard_fires_graveyard_payoff(text):
    assert "graveyard_payoff" in keys(text)


def test_original_word_order_still_fires():
    # "from your graveyard … battlefield" ordering (pre-#133 behaviour)
    text = "Return target creature card from your graveyard to the battlefield."
    assert "graveyard_payoff" in keys(text)


@pytest.mark.parametrize(
    "text",
    [
        "Lightning Bolt deals 3 damage to any target.",
        # cast-from-EXILE must not fire the graveyard key
        "You may cast the exiled card without paying its mana cost.",
        # graveyard hate mentions the zone but casts nothing from it
        "Exile target card from a graveyard.",
    ],
    ids=["bolt", "cast-from-exile", "graveyard-hate"],
)
def test_negatives_do_not_fire(text):
    assert "graveyard_payoff" not in keys(text)
