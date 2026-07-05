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


# ── cast triggers: "a player casts" templating (issue #134) ──────────────────

NIV_MIZZET_PARUN = (
    "This spell can't be countered.\nFlying\n"
    "Whenever you draw a card, Niv-Mizzet deals 1 damage to any target.\n"
    "Whenever a player casts an instant or sorcery spell, you draw a card."
)


def test_a_player_casts_fires_type_trigger():
    assert "cast_trigger_instant_sorcery" in keys(NIV_MIZZET_PARUN)


def test_you_cast_still_fires():
    assert "cast_trigger_creature" in keys("Whenever you cast a creature spell, draw a card.")


def test_opponent_casts_is_punisher_not_consumer():
    # The deck can't feed an opponent-cast trigger — must NOT fire.
    text = "Whenever an opponent casts a creature spell, you gain 1 life."
    assert "cast_trigger_creature" not in keys(text)


def test_a_player_casts_punisher_does_not_fire():
    # Ruric Thar: symmetric trigger whose payoff PUNISHES the caster —
    # this deck avoids noncreature spells, so no consumer key.
    text = (
        "Whenever a player casts a noncreature spell, Ruric Thar, the "
        "Unbowed deals 6 damage to them."
    )
    assert "cast_trigger_instant_sorcery" not in keys(text)


# ── high_mv_payoff: Kozilek's discard-MV-X clause (issue #134) ────────────────

KOZILEK_DISTORTION = (
    "When you cast this spell, if you have fewer than seven cards in hand, "
    "draw cards equal to the difference.\nMenace\n"
    "Discard a card with mana value X: Counter target spell with mana value X."
)


def test_kozilek_fires_high_mv_payoff():
    assert "high_mv_payoff" in keys(KOZILEK_DISTORTION)


def test_zhulodok_still_fires_high_mv_payoff():
    text = (
        "Whenever you cast your first spell during each of your turns, if it "
        "has mana value 7 or greater, it gains cascade."
    )
    assert "high_mv_payoff" in keys(text)


def test_plain_discard_does_not_fire_high_mv():
    assert "high_mv_payoff" not in keys("Discard a card: Draw a card.")


# ── activated tutor engines (issue #135) ─────────────────────────────────────

YISAN = (
    "{2}{G}, {T}, Put a verse counter on Yisan: Search your library for a "
    "creature card with mana value equal to the number of verse counters on "
    "Yisan, put it onto the battlefield, then shuffle."
)
CAPTAIN_SISAY = (
    "{T}: Search your library for a legendary card, reveal it, put it into "
    "your hand, then shuffle."
)


def test_yisan_fires_creature_tutor_engine():
    ks = keys(YISAN)
    assert "activated_tutor_creature" in ks
    assert "activated_tutor" in ks


def test_sisay_fires_generic_tutor_engine_only():
    ks = keys(CAPTAIN_SISAY)
    assert "activated_tutor" in ks
    assert "activated_tutor_creature" not in ks


@pytest.mark.parametrize(
    "text",
    [
        "{T}: Add {G}.",
        # triggered (ETB) search is not an activation loop
        "When this creature enters, search your library for a basic land card.",
        # activation exists but effect in a later sentence isn't a search
        "{T}: Draw a card. Then each player searches their library for a card.",
    ],
    ids=["mana-ability", "etb-search", "activation-then-symmetric-search"],
)
def test_non_engine_abilities_do_not_fire(text):
    assert "activated_tutor" not in keys(text)
    assert "activated_tutor_creature" not in keys(text)


# ── anthem effects (issue #136 tranche 2) ────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("Other creatures you control get +1/+1.", "static_pump"),           # Kongming
        ("Other Snake creatures you control get +0/+1.", "static_pump"),     # Sachi (tribe word)
        ("{2}{G}{G}{G}: Creatures you control get +3/+3 and gain trample until end of turn.",
         "static_pump"),                                                     # Kamahl (activated)
        ("Creatures you control have flying.", "keyword_grant"),
        ("Zombie creatures you control have menace.", "keyword_grant"),
    ],
    ids=["kongming", "sachi-tribal", "kamahl-activated", "flying-grant", "tribal-keyword"],
)
def test_anthems_fire(text, key):
    assert key in keys(text)


def test_type_only_lord_is_tribals_domain():
    # "Other Vampires you control …" (no word "creatures") is a tribal
    # lord — tribal_vampire's consumer SQL covers it; keyword_grant
    # deliberately stays out.
    ks = keys("Other Vampires you control have deathtouch.")
    assert "tribal_vampire" in ks
    assert "keyword_grant" not in ks


@pytest.mark.parametrize(
    "text",
    [
        # opponent-facing debuff only — no anthem
        "Creatures your opponents control get -2/-2.",
        # single-target pump
        "Target creature gets +3/+3 until end of turn.",
        # granting a non-keyword ability
        "Shamans you control have \"{T}: Add {G}{G}.\"",
    ],
    ids=["opponent-debuff", "single-target", "non-keyword-grant"],
)
def test_non_anthems_do_not_fire(text):
    ks = keys(text)
    assert "static_pump" not in ks
    assert "keyword_grant" not in ks
