"""Unit tests for iterative heuristic overlay helpers — issue #61.

Covers:
  - is_removal_card()     — hard and soft removal detection
  - is_value_card()       — draw / tutor / loot detection
  - is_evasion_card()     — unblockability detection
  - composition_targets   — fallback defaults when no profile file exists
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from ops.deck.removal      import is_removal_card      # noqa: E402
from ops.deck.value_engine import is_value_card        # noqa: E402
from ops.deck.evasion      import is_evasion_card      # noqa: E402


# ── is_removal_card ───────────────────────────────────────────────────────────

class TestIsRemovalCard:
    def test_exile_target_creature(self):
        assert is_removal_card("Exile target creature.")

    def test_destroy_target_permanent(self):
        assert is_removal_card("Destroy target nonland permanent.")

    def test_destroy_all_creatures(self):
        assert is_removal_card("Destroy all creatures.")

    def test_exile_all_creatures(self):
        assert is_removal_card("Exile all nonland permanents.")

    def test_bounce_to_hand(self):
        assert is_removal_card(
            "Return target creature to its owner's hand."
        )

    def test_tuck(self):
        assert is_removal_card(
            "Return target creature to the bottom of its owner's library."
        )

    def test_counter_spell(self):
        assert is_removal_card("Counter target spell.")

    def test_negative(self):
        assert not is_removal_card("Draw two cards. Then discard two cards.")

    def test_empty_string(self):
        assert not is_removal_card("")

    def test_graveyard_recursion_not_removal(self):
        # "exile" appearing in a context that targets the graveyard is recursion
        assert not is_removal_card(
            "Return target creature card from your graveyard to the battlefield."
        )


# ── is_value_card ─────────────────────────────────────────────────────────────

class TestIsValueCard:
    def test_draw_a_card(self):
        assert is_value_card("Draw a card.")

    def test_draw_two_cards(self):
        assert is_value_card("Draw two cards.")

    def test_draw_x_cards(self):
        assert is_value_card("Draw X cards.")

    def test_tutor(self):
        assert is_value_card(
            "Search your library for a creature card, reveal it, "
            "put it into your hand, then shuffle."
        )

    def test_loot(self):
        assert is_value_card("Draw a card, then discard a card.")

    def test_reminder_text_stripped(self):
        # Reminder text in parentheses should NOT trigger a match.
        # Example: a card whose reminder text says "(draw a card)" but the
        # card's actual oracle text doesn't draw.
        assert not is_value_card("(When this permanent enters, you may draw a card.)")

    def test_negative(self):
        assert not is_value_card("Destroy target creature.")

    def test_empty_string(self):
        assert not is_value_card("")


# ── is_evasion_card ───────────────────────────────────────────────────────────

class TestIsEvasionCard:
    def test_unblockable(self):
        assert is_evasion_card(
            "Target creature can't be blocked this turn."
        )

    def test_becomes_unblockable(self):
        assert is_evasion_card("Target creature is unblockable until end of turn.")

    def test_small_evasion(self):
        assert is_evasion_card(
            "Creatures with power 2 or greater can't block this creature."
        )

    def test_negative_no_pattern(self):
        assert not is_evasion_card("Draw two cards.")

    def test_negative_flying_keyword_only(self):
        # "Flying" as a lone keyword word is NOT matched by the evasion pattern.
        # The pattern looks for "can't be blocked" style text, not the keyword name.
        assert not is_evasion_card("Flying\nWhenever this creature deals combat damage to a player, draw a card.")

    def test_empty_string(self):
        assert not is_evasion_card("")


# ── composition_targets fallback ─────────────────────────────────────────────

class TestCompositionTargets:
    def test_fallback_defaults_present(self, tmp_path, monkeypatch):
        """When no profile file exists TARGETS should equal _DEFAULTS."""
        import importlib
        import ops.deck.composition_targets as ct

        monkeypatch.setattr(ct, "PROFILE_PATH", tmp_path / "missing.json")
        targets = ct._load()
        assert targets["removal"]  >= 1
        assert targets["draw"]     >= 1
        assert targets["evasion"]  >= 1
        assert targets["ramp"]     >= 1

    def test_profile_file_overrides_defaults(self, tmp_path, monkeypatch):
        """Values in the profile JSON replace the corresponding defaults."""
        import json
        import ops.deck.composition_targets as ct

        profile = {
            "targets": {
                "global": {"removal": 99, "draw": 77, "evasion": 55}
            }
        }
        profile_file = tmp_path / "profile.json"
        profile_file.write_text(json.dumps(profile))

        monkeypatch.setattr(ct, "PROFILE_PATH", profile_file)
        targets = ct._load()
        assert targets["removal"] == 99
        assert targets["draw"]    == 77
        assert targets["evasion"] == 55
        # Keys not in the file fall back to defaults
        assert targets["ramp"] == ct._DEFAULTS["ramp"]

    def test_malformed_profile_uses_defaults(self, tmp_path, monkeypatch):
        """A corrupt profile file silently falls back to defaults."""
        import ops.deck.composition_targets as ct

        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not json {{{")

        monkeypatch.setattr(ct, "PROFILE_PATH", bad_file)
        targets = ct._load()
        assert targets == ct._DEFAULTS
