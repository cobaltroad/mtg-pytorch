"""Tests for deck archetype detection in import_utils.py.

Validates that detect_archetype() correctly identifies deck archetypes from
card composition, using synthetic card-data dicts that mimic real oracle text.

Acceptance criteria (per the issue):
  - zombie aristocrats → combo or aristocrats win-condition
  - elf stompy → aggro
  - token swarm → tokens
  - reanimator shell → reanimator
  - stax prison → stax
  - punisher (Y'shtola style) → punisher
  - midrange (Atraxa value) → midrange
  - spellslinger (storm) → spellslinger
  - control (wrath-heavy) → control
  - avg_cmc and role_counts are populated correctly
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Allow importing from the parent ingest directory
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from import_utils import detect_archetype  # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────────────

def _creature(name: str, cmc: float = 2.0, oracle: str = "") -> dict:
    return {"type_line": "Creature — Zombie", "cmc": cmc, "oracle_text": oracle, "keywords": []}


def _spell(type_line: str, cmc: float, oracle: str = "") -> dict:
    return {"type_line": type_line, "cmc": cmc, "oracle_text": oracle, "keywords": []}


def _land(oracle: str = "{T}: Add {B}.") -> dict:
    return {"type_line": "Land", "cmc": 0, "oracle_text": oracle, "keywords": []}


def _artifact(oracle: str, cmc: float = 2.0) -> dict:
    return {"type_line": "Artifact", "cmc": cmc, "oracle_text": oracle, "keywords": []}


def _tutor(cmc: float = 2.0) -> dict:
    return _spell("Sorcery", cmc, "Search your library for a card, put it into your hand, then shuffle.")


def _counterspell(cmc: float = 2.0) -> dict:
    return _spell("Instant", cmc, "Counter target spell.")


def _sweeper(cmc: float = 5.0) -> dict:
    return _spell("Sorcery", cmc, "Destroy all creatures.")


def _wrath(cmc: float = 4.0) -> dict:
    return _spell("Sorcery", cmc, "Destroy all creatures. They can't be regenerated.")


def _token_spell() -> dict:
    return _spell("Sorcery", 3.0, "Create three 2/2 black Zombie creature tokens.")


def _reanimation_spell() -> dict:
    return _spell("Sorcery", 3.0,
                  "Return target creature card from your graveyard to the battlefield.")


def _aristocrat_trigger(cmc: float = 2.0) -> dict:
    return _creature("Aristocrat", cmc,
                     "Whenever another creature dies, you gain 1 life and deal 1 damage to any target.")


def _stax_piece(oracle: str) -> dict:
    return {"type_line": "Enchantment", "cmc": 3.0, "oracle_text": oracle, "keywords": []}


def _punisher_piece(oracle: str) -> dict:
    return {"type_line": "Enchantment", "cmc": 3.0, "oracle_text": oracle, "keywords": []}


def _draw_spell(cmc: float = 3.0) -> dict:
    return _spell("Sorcery", cmc, "Draw three cards.")


def _ramp_card() -> dict:
    return _artifact("{T}: Add {G}{G}.", cmc=2.0)


def _anthem_card() -> dict:
    return {"type_line": "Enchantment", "cmc": 3.0,
            "oracle_text": "Creatures you control get +1/+1.", "keywords": []}


def _infect_creature() -> dict:
    return {"type_line": "Creature — Phyrexian", "cmc": 2.0,
            "oracle_text": "Infect (This creature deals damage to creatures in the form of "
                           "-1/-1 counters and to players in the form of poison counters.)",
            "keywords": ["Infect"]}


def _magecraft_creature(cmc: float = 3.0) -> dict:
    return _creature("Wizard", cmc,
                     "Magecraft — Whenever you cast or copy an instant or sorcery spell, "
                     "this creature gets +1/+1 until end of turn.")


def _storm_spell() -> dict:
    return _spell("Sorcery", 5.0,
                  "When you cast this spell, copy it for each spell cast before it this turn. "
                  "Storm (When you cast this spell, copy it for each spell cast before it this turn.)")


# ── Test: empty deck ─────────────────────────────────────────────────────────

def test_empty_deck():
    result = detect_archetype([])
    assert result["archetype"] == "unknown"
    assert result["win_conditions"] == []
    assert result["avg_cmc"] == 0.0
    assert result["role_counts"]["ramp"] == 0


# ── Test: avg_cmc excludes lands ──────────────────────────────────────────────

def test_avg_cmc_excludes_lands():
    cards = [
        _land(),
        _spell("Sorcery", 2.0, "Draw a card."),
        _spell("Sorcery", 4.0, "Draw three cards."),
    ]
    result = detect_archetype(cards)
    # avg of [2.0, 4.0] = 3.0; land should be excluded
    assert result["avg_cmc"] == 3.0


# ── Test: role_counts are populated ──────────────────────────────────────────

def test_role_counts_ramp():
    cards = [_ramp_card()] * 10 + [_land()] * 10
    result = detect_archetype(cards)
    assert result["role_counts"]["ramp"] >= 10


def test_role_counts_tutor():
    cards = [_tutor()] * 5
    result = detect_archetype(cards)
    assert result["role_counts"]["tutor"] >= 5


def test_role_counts_draw():
    cards = [_draw_spell()] * 8
    result = detect_archetype(cards)
    assert result["role_counts"]["draw"] >= 8


def test_role_counts_removal():
    cards = [_sweeper()] * 6 + [_wrath()] * 6
    result = detect_archetype(cards)
    assert result["role_counts"]["removal"] >= 10


# ── Archetype: tokens ────────────────────────────────────────────────────────

class TestTokens:
    """≥ 8 token-creation effects → tokens archetype."""

    def test_tokens_archetype(self):
        cards = [_token_spell()] * 10 + [_creature("Zombie")] * 20 + [_land()] * 36
        result = detect_archetype(cards)
        assert result["archetype"] == "tokens"

    def test_tokens_threshold(self):
        # Exactly 8 token spells → tokens
        cards = [_token_spell()] * 8 + [_creature("Zombie")] * 20 + [_land()] * 38
        result = detect_archetype(cards)
        assert result["archetype"] == "tokens"

    def test_below_tokens_threshold(self):
        # Only 5 token spells — should not be classified as tokens
        cards = [_token_spell()] * 5 + [_creature("Zombie")] * 20 + [_land()] * 40
        result = detect_archetype(cards)
        assert result["archetype"] != "tokens"


# ── Archetype: reanimator ─────────────────────────────────────────────────────

class TestReanimator:
    """≥ 5 graveyard-return effects → reanimator archetype."""

    def test_reanimator_archetype(self):
        cards = [_reanimation_spell()] * 6 + [_creature("Zombie")] * 20 + [_land()] * 36
        result = detect_archetype(cards)
        assert result["archetype"] == "reanimator"


# ── Archetype: stax ───────────────────────────────────────────────────────────

class TestStax:
    """≥ 5 stax-effect cards → stax archetype."""

    STAX_ORACLE = [
        "Opponents can't cast spells during your turn.",
        "Players can't cast more than one spell each turn.",
        "Spells cost {2} more to cast.",
        "Unless its controller pays {1}, counter that spell.",
        "Each opponent skips their draw step.",
        "Players can't untap more than two permanents each turn.",
    ]

    def test_stax_archetype(self):
        cards = [_stax_piece(o) for o in self.STAX_ORACLE] + \
                [_creature("Hatebear")] * 15 + [_land()] * 37
        result = detect_archetype(cards)
        assert result["archetype"] == "stax"


# ── Archetype: punisher ───────────────────────────────────────────────────────

class TestPunisher:
    """≥ 5 punisher-effect cards → punisher archetype."""

    PUNISHER_ORACLE = [
        "Whenever an opponent casts a spell, that player loses 1 life.",
        "Whenever a player draws a card, they lose 1 life.",
        "Each opponent loses 1 life at the beginning of each upkeep.",
        "Whenever an opponent gains life, each opponent loses 2 life.",
        "Each player loses 1 life for each card they draw.",
    ]

    def test_punisher_archetype(self):
        cards = [_punisher_piece(o) for o in self.PUNISHER_ORACLE] + \
                [_creature("Life")] * 15 + [_land()] * 37
        result = detect_archetype(cards)
        assert result["archetype"] == "punisher"


# ── Archetype: combo ──────────────────────────────────────────────────────────

class TestCombo:
    """≥ 3 tutors + win-condition cards → combo archetype."""

    def test_combo_with_infect(self):
        cards = (
            [_tutor()] * 4
            + [_infect_creature()] * 5
            + [_creature("Zombie")] * 18
            + [_land()] * 36
        )
        result = detect_archetype(cards)
        assert result["archetype"] == "combo"
        assert "infect" in result["win_conditions"]

    def test_combo_with_aristocrats(self):
        cards = (
            [_tutor()] * 4
            + [_aristocrat_trigger()] * 8
            + [_creature("Zombie")] * 15
            + [_land()] * 36
        )
        result = detect_archetype(cards)
        assert result["archetype"] == "combo"
        assert "aristocrats" in result["win_conditions"]

    def test_combo_with_storm(self):
        cards = (
            [_tutor()] * 4
            + [_storm_spell()] * 3
            + [_spell("Instant", 1.0, "Draw a card.")] * 10
            + [_land()] * 36
        )
        result = detect_archetype(cards)
        assert result["archetype"] == "combo"
        assert "storm" in result["win_conditions"]


# ── Archetype: aggro ──────────────────────────────────────────────────────────

class TestAggro:
    """≥ 35 creatures + low avg CMC + anthems → aggro."""

    def test_elf_stompy(self):
        # 36 creatures at avg CMC ~1.5, with 4 anthem effects
        low_cmc_elf = _creature("Elf", cmc=1.0, oracle="{T}: Add {G}.")
        cards = (
            [low_cmc_elf] * 36
            + [_anthem_card()] * 4
            + [_land()] * 26
        )
        result = detect_archetype(cards)
        assert result["archetype"] == "aggro"
        # avg_cmc = (36 * 1.0 + 4 * 3.0) / 40 = 48 / 40 = 1.2
        assert result["avg_cmc"] == 1.2
        assert result["avg_cmc"] <= 2.8


# ── Archetype: spellslinger ───────────────────────────────────────────────────

class TestSpellslinger:
    """≥ 10 instants/sorceries + magecraft/storm payoffs → spellslinger."""

    def test_spellslinger_archetype(self):
        cards = (
            [_spell("Instant", 1.0, "Draw a card.")] * 12
            + [_magecraft_creature()] * 4
            + [_creature("Wizard")] * 10
            + [_land()] * 37
        )
        result = detect_archetype(cards)
        assert result["archetype"] == "spellslinger"


# ── Archetype: control ────────────────────────────────────────────────────────

class TestControl:
    """≥ 18 sweepers/removal + ≥ 8 counterspells → control."""

    def test_control_archetype(self):
        cards = (
            [_sweeper()] * 10
            + [_wrath()] * 10
            + [_counterspell()] * 9
            + [_draw_spell()] * 8
            + [_land()] * 30
        )
        result = detect_archetype(cards)
        assert result["archetype"] == "control"


# ── Archetype: midrange ───────────────────────────────────────────────────────

class TestMidrange:
    """High draw count but not aggro/control → midrange."""

    def test_midrange_atraxa_style(self):
        # Atraxa-style: lots of draw, moderate creatures, moderate CMC
        cards = (
            [_draw_spell(cmc=3.0)] * 12
            + [_creature("Merfolk", cmc=3.0)] * 20
            + [_ramp_card()] * 8
            + [_land()] * 28
        )
        result = detect_archetype(cards)
        assert result["archetype"] == "midrange"


# ── Win-condition detection ───────────────────────────────────────────────────

class TestWinConditions:
    def test_infect_keyword(self):
        cards = [_infect_creature()] * 3 + [_creature("Zombie")] * 30 + [_land()] * 36
        result = detect_archetype(cards)
        assert "infect" in result["win_conditions"]

    def test_group_slug(self):
        slug = _spell("Enchantment", 3.0,
                      "Each opponent loses 1 life at the beginning of each of their upkeeps. "
                      "Each player loses 1 life whenever they draw a card.")
        cards = [slug] * 5 + [_creature("Zombie")] * 20 + [_land()] * 36
        result = detect_archetype(cards)
        assert "group_slug" in result["win_conditions"]

    def test_lifegain_win_condition(self):
        lifegain = _spell("Enchantment", 2.0,
                          "Whenever you gain life, each opponent loses that much life.")
        cards = [lifegain] * 5 + [_creature("Angel")] * 20 + [_land()] * 36
        result = detect_archetype(cards)
        assert "lifegain" in result["win_conditions"]

    def test_storm_win_condition(self):
        cards = [_storm_spell()] * 5 + [_spell("Sorcery", 1.0, "Draw a card.")] * 20 + [_land()] * 36
        result = detect_archetype(cards)
        assert "storm" in result["win_conditions"]


# ── partner deck-name splitting (#147) ────────────────────────────────────────


def test_split_partner_names():
    from import_decklists import _split_partner_names

    assert _split_partner_names("Tymna the Weaver // Thrasios, Triton Hero") == [
        "Tymna the Weaver", "Thrasios, Triton Hero",
    ]
    assert _split_partner_names("Tymna the Weaver / Thrasios, Triton Hero") == [
        "Tymna the Weaver", "Thrasios, Triton Hero",
    ]
    assert _split_partner_names("Atraxa, Praetors' Voice") == [
        "Atraxa, Praetors' Voice",
    ]
