"""Unit tests for analyze_commander_oracle_text().

All tests are pure — no database, no model inference.  They exercise the
signal extraction pipeline against known oracle texts for canonical commanders.

Acceptance criteria (from the issue):
  - Tyvar the Bellicose: tribal:Elf ✅, combat ✅, deathtouch ✅,
    +1/+1 counters ✅, mana ability ⚠️ (recognized, boost applied)
  - Lathril, Blade of the Elves: tribal:Elf ✅, combat ✅, menace ✅,
    token creation ✅  — no gaps from recognized terms
  - A commander with unrecognized mechanics shows ❓ and
    "consider adding decklists" message
  - Fynn the Fangbearer: deathtouch signal → deathtouch boost applied
  - Rocco, Street Chef: play_from_exile, Food, +1/+1 counters signals
  - Atraxa Grand Unifier: multi-card-type signals
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Allow importing from the api ops directory
sys.path.insert(0, str(Path(__file__).parent.parent))

from ops.commander_analysis import analyze_commander_oracle_text  # noqa: E402


# ── Oracle texts (abbreviated) ────────────────────────────────────────────────

TYVAR_ORACLE = (
    "Whenever one or more Elves you control attack, they gain deathtouch until end of turn.\n"
    "Each creature you control has 'Whenever a mana ability of this creature resolves, "
    "put a number of +1/+1 counters on it equal to the amount of mana this creature produced.'"
)

LATHRIL_ORACLE = (
    "Menace\n"
    "Whenever Lathril, Blade of the Elves deals combat damage to a player, "
    "create that many 1/1 green Elf Warrior creature tokens.\n"
    "Tap ten untapped Elves you control: Each opponent loses 10 life and you gain 10 life."
)

FYNN_ORACLE = (
    "Deathtouch\n"
    "Whenever a creature you control with deathtouch deals combat damage to a player, "
    "that player gets two poison counters."
)

ROCCO_ORACLE = (
    "Whenever Rocco, Street Chef enters the battlefield or attacks, "
    "each player creates a Food token.\n"
    "Creatures you control have '{1}, Sacrifice a Food: Put a +1/+1 counter on this creature.'"
)

ATRAXA_ORACLE = (
    "Flying, vigilance, deathtouch, lifelink\n"
    "At the beginning of your end step, proliferate.\n"
    "When Atraxa, Grand Unifier enters the battlefield, reveal the top ten cards of your library. "
    "Put any number of creature cards, instant cards, sorcery cards, artifact cards, "
    "enchantment cards, and/or planeswalker cards from among them into your hand. "
    "Put the rest on the bottom of your library in a random order."
)

DUNGEON_ORACLE = (
    "Whenever you venture into the dungeon, put a +1/+1 counter on target creature you control.\n"
    "Whenever you complete a dungeon, you may cast target creature card from your graveyard "
    "without paying its mana cost."
)

GENERIC_UNRECOGNIZED_ORACLE = (
    "Whenever a player casts their third spell each turn, frazzlewick that permanent.\n"
    "If you have frobulated this turn, draw a card."
)


# ── Helper ────────────────────────────────────────────────────────────────────

def _signal_types(analysis) -> list[str]:
    return [s.signal_type for s in analysis.signals]


def _labels(analysis) -> list[str]:
    return [s.label for s in analysis.signals]


def _boosts(analysis) -> set[str]:
    return set(analysis.boost_overrides)


# ── Tyvar the Bellicose ───────────────────────────────────────────────────────

class TestTyvarTheBellicose:
    def setup_method(self):
        self.analysis = analyze_commander_oracle_text(
            oracle_text=TYVAR_ORACLE,
            commander_name="Tyvar the Bellicose",
            color_identity=["B", "G"],
        )

    def test_commander_name_preserved(self):
        assert self.analysis.commander_name == "Tyvar the Bellicose"

    def test_color_identity_preserved(self):
        assert self.analysis.color_identity == ["B", "G"]

    def test_tribal_elf_detected(self):
        labels = _labels(self.analysis)
        assert any("Elf" in lbl for lbl in labels), f"No Elf signal in {labels}"

    def test_combat_attack_detected(self):
        types = _signal_types(self.analysis)
        assert "combat" in types, f"No combat signal in {types}"

    def test_deathtouch_granted_detected(self):
        labels = _labels(self.analysis)
        assert any("deathtouch" in lbl.lower() for lbl in labels), (
            f"No deathtouch signal in {labels}"
        )

    def test_counter_synergy_detected(self):
        types = _signal_types(self.analysis)
        assert "counter" in types, f"No counter signal in {types}"

    def test_mana_ability_detected(self):
        """Key acceptance criterion: 'mana ability' is a MTG rules term and must be detected."""
        labels = _labels(self.analysis)
        assert any("mana" in lbl.lower() for lbl in labels), (
            f"'mana ability' MTG rules term not detected in signals: {labels}"
        )

    def test_mana_ability_boost_applied(self):
        """mana_producers boost must be active for Tyvar."""
        assert "mana_producers" in _boosts(self.analysis), (
            f"mana_producers boost not active, boosts={_boosts(self.analysis)}"
        )

    def test_archetype_hint_includes_elfball(self):
        hint = (self.analysis.archetype_hint or "").lower()
        assert "elf" in hint or "elfball" in hint or "mana" in hint, (
            f"Archetype hint does not mention elf/elfball/mana: {hint!r}"
        )

    def test_generation_confidence_not_none(self):
        assert self.analysis.generation_confidence != "none"


# ── Lathril, Blade of the Elves ───────────────────────────────────────────────

class TestLathrilBladeOfTheElves:
    def setup_method(self):
        self.analysis = analyze_commander_oracle_text(
            oracle_text=LATHRIL_ORACLE,
            commander_name="Lathril, Blade of the Elves",
            color_identity=["B", "G"],
            keywords=["Menace"],
        )

    def test_tribal_elf_detected(self):
        labels = _labels(self.analysis)
        assert any("Elf" in lbl for lbl in labels)

    def test_combat_detected(self):
        types = _signal_types(self.analysis)
        assert "combat" in types

    def test_menace_detected(self):
        labels = _labels(self.analysis)
        assert any("menace" in lbl.lower() for lbl in labels)

    def test_token_creation_detected(self):
        types = _signal_types(self.analysis)
        assert "token" in types, f"No token signal in {types}"

    def test_archetype_hint_set(self):
        assert self.analysis.archetype_hint is not None

    def test_generation_confidence_high_or_medium(self):
        assert self.analysis.generation_confidence in ("high", "medium")


# ── Fynn the Fangbearer ───────────────────────────────────────────────────────

class TestFynnTheFangbearer:
    def setup_method(self):
        self.analysis = analyze_commander_oracle_text(
            oracle_text=FYNN_ORACLE,
            commander_name="Fynn the Fangbearer",
            color_identity=["G"],
            keywords=["Deathtouch"],
        )

    def test_deathtouch_detected(self):
        labels = _labels(self.analysis)
        assert any("deathtouch" in lbl.lower() for lbl in labels)

    def test_deathtouch_boost_applied(self):
        assert "deathtouch" in _boosts(self.analysis)

    def test_combat_damage_detected(self):
        types = _signal_types(self.analysis)
        assert "combat" in types


# ── Rocco, Street Chef ────────────────────────────────────────────────────────

class TestRoccoStreetChef:
    def setup_method(self):
        self.analysis = analyze_commander_oracle_text(
            oracle_text=ROCCO_ORACLE,
            commander_name="Rocco, Street Chef",
            color_identity=["G", "R", "W"],
        )

    def test_food_token_detected(self):
        labels = _labels(self.analysis)
        assert any("food" in lbl.lower() for lbl in labels), (
            f"No Food signal in {labels}"
        )

    def test_counter_synergy_detected(self):
        types = _signal_types(self.analysis)
        assert "counter" in types

    def test_token_signal_present(self):
        types = _signal_types(self.analysis)
        assert "token" in types or "mechanic" in types


# ── Atraxa Grand Unifier ─────────────────────────────────────────────────────

class TestAtraxaGrandUnifier:
    def setup_method(self):
        self.analysis = analyze_commander_oracle_text(
            oracle_text=ATRAXA_ORACLE,
            commander_name="Atraxa, Grand Unifier",
            color_identity=["B", "G", "U", "W"],
            keywords=["Flying", "Vigilance", "Deathtouch", "Lifelink"],
        )

    def test_proliferate_detected(self):
        labels = _labels(self.analysis)
        assert any("proliferate" in lbl.lower() for lbl in labels)

    def test_card_type_signals_detected(self):
        """Atraxa cares about multiple card types — at least some should surface."""
        labels = [lbl.lower() for lbl in _labels(self.analysis)]
        card_type_signals = [
            lbl for lbl in labels
            if any(t in lbl for t in ("instant", "sorcery", "artifact", "enchantment", "planeswalker", "creature"))
        ]
        assert len(card_type_signals) >= 2, (
            f"Expected multiple card-type signals, got: {card_type_signals}"
        )

    def test_multiple_evasion_keywords(self):
        types = _signal_types(self.analysis)
        assert "evasion" in types


# ── Dungeon commander ─────────────────────────────────────────────────────────

class TestDungeonCommander:
    def setup_method(self):
        self.analysis = analyze_commander_oracle_text(
            oracle_text=DUNGEON_ORACLE,
            commander_name="Dungeon Commander",
            color_identity=["U", "B"],
        )

    def test_dungeon_term_recognized(self):
        """'complete a dungeon' and 'venture into the dungeon' should appear as signals."""
        labels = [lbl.lower() for lbl in _labels(self.analysis)]
        assert any("dungeon" in lbl for lbl in labels), (
            f"Dungeon term not recognized in signals: {labels}"
        )

    def test_dungeon_no_boost(self):
        """Dungeon mechanics have no generation boost → appear in gaps too."""
        assert any("dungeon" in g.lower() for g in self.analysis.gaps), (
            f"Dungeon gap not listed: {self.analysis.gaps}"
        )


# ── Unrecognized oracle text ───────────────────────────────────────────────────

class TestUnrecognizedOracle:
    def setup_method(self):
        self.analysis = analyze_commander_oracle_text(
            oracle_text=GENERIC_UNRECOGNIZED_ORACLE,
            commander_name="Mystery Commander",
            color_identity=["U"],
        )

    def test_has_gaps(self):
        """Completely unrecognized mechanics should produce gap entries."""
        assert len(self.analysis.gaps) > 0, "Expected gaps for unrecognized oracle text"

    def test_generation_confidence_not_high(self):
        assert self.analysis.generation_confidence in ("low", "none", "medium")


# ── Empty oracle text ─────────────────────────────────────────────────────────

class TestEmptyOracle:
    def setup_method(self):
        self.analysis = analyze_commander_oracle_text(
            oracle_text="",
            commander_name="Vanilla Commander",
            color_identity=["W"],
        )

    def test_no_signals(self):
        assert self.analysis.signals == []

    def test_no_gaps(self):
        assert self.analysis.gaps == []

    def test_generation_confidence_none(self):
        assert self.analysis.generation_confidence == "none"

    def test_no_archetype_hint(self):
        assert self.analysis.archetype_hint is None

    def test_boost_overrides_empty(self):
        assert self.analysis.boost_overrides == []


# ── RULES_TERM_SIGNALS dictionary ────────────────────────────────────────────

def test_rules_term_signals_documented():
    """Every entry in RULES_TERM_SIGNALS must have all required fields."""
    from ops.commander_analysis import RULES_TERM_SIGNALS
    for phrase, term in RULES_TERM_SIGNALS.items():
        assert term.signal_type, f"Missing signal_type for '{phrase}'"
        assert term.label, f"Missing label for '{phrase}'"
        assert term.confidence in ("high", "medium", "low", "unknown"), (
            f"Invalid confidence {term.confidence!r} for '{phrase}'"
        )
        # boost can be None (means "recognized but no boost") — that's fine


def test_mana_ability_in_rules_terms():
    from ops.commander_analysis import RULES_TERM_SIGNALS
    assert "mana ability" in RULES_TERM_SIGNALS, (
        "'mana ability' must be in RULES_TERM_SIGNALS (canonical Tyvar test case)"
    )
    term = RULES_TERM_SIGNALS["mana ability"]
    assert term.boost == "mana_producers"
