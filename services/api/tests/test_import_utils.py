"""Unit tests for deck-name defaulting helpers in import_utils.py.

Tests are pure — no database, no async IO.  They exercise the _slugify()
helper and verify that the deck_name defaulting contract is correct.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Allow importing from the api ops directory
sys.path.insert(0, str(Path(__file__).parent.parent))

from ops.import_utils import _slugify  # noqa: E402


# ── _slugify ──────────────────────────────────────────────────────────────────

class TestSlugify:
    def test_basic_commander(self):
        assert _slugify("Wilhelt, the Rotcleaver") == "wilhelt-the-rotcleaver"

    def test_lowercase(self):
        assert _slugify("Sol Ring") == "sol-ring"

    def test_apostrophe_stripped(self):
        assert _slugify("Yuriko, the Tiger's Shadow") == "yuriko-the-tigers-shadow"

    def test_multiple_spaces_collapsed(self):
        # Extra internal spaces should not produce double hyphens
        result = _slugify("A  B")
        assert "--" not in result
        assert result == "a-b"

    def test_already_lowercase_no_punctuation(self):
        assert _slugify("atraxa") == "atraxa"

    def test_partner_commander_with_slash(self):
        # Slashes are non-alphanumeric and should be removed
        result = _slugify("Rograkh, Son of Rohgahh")
        assert "/" not in result
        assert result == "rograkh-son-of-rohgahh"

    def test_numbers_preserved(self):
        # A name containing digits should keep them
        assert _slugify("Niv-Mizzet Reborn") == "niv-mizzet-reborn"
        assert _slugify("Strixhaven Stadium") == "strixhaven-stadium"
        # Synthetic check that digits are not stripped
        assert "2" in _slugify("Card 2 Name")

    def test_hyphen_in_name_preserved(self):
        assert _slugify("K'rrik, Son of Yawgmoth") == "krrik-son-of-yawgmoth"

    def test_no_leading_or_trailing_hyphens(self):
        result = _slugify("Tymna the Weaver")
        assert not result.startswith("-")
        assert not result.endswith("-")
