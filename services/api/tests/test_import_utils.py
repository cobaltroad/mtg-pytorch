"""Unit tests for import_utils.py (pure, no database, no async IO).

Covers:
  - _slugify()            — lowercase hyphenated slug generation
  - parse_moxfield_txt()  — decklist parsing (headerless and sectioned formats)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Allow importing from the api ops directory
sys.path.insert(0, str(Path(__file__).parent.parent))

from ops.import_utils import _slugify, parse_moxfield_txt  # noqa: E402


# ── Fixture: full Adeline, Resplendent Cathar decklist ────────────────────────
# Source: Moxfield export, headerless format — commander is the last card,
# separated from the maindeck by a blank line.

_ADELINE_DECKLIST = """\
1 Aerial Extortionist
1 Ainok Strike Leader
1 Akroma's Will
1 Anointed Procession
1 Arcane Signet
1 Austere Command
1 Aven Interrupter
1 Bennie Bracks, Zoologist
1 Bitterthorn, Nissa's Animus
1 Bonders' Enclave
1 Buried Ruin
1 Call the Coppercoats
1 Caretaker's Talent
1 Cathars' Crusade
1 Cavern of Souls
1 Charismatic Conqueror
1 Clever Concealment
1 Combat Calligrapher
1 Commander's Plate
1 Defiler of Faith
1 Demolition Field
1 Divine Visitation
1 Dolmen Gate
1 Don't Move
1 Eldrazi Monument
1 Ellyn Harbreeze, Busybody
1 Elspeth, Storm Slayer
1 Elspeth, Sun's Champion
1 Emeria, the Sky Ruin
1 Enduring Innocence
1 Excise the Imperfect
1 Flawless Maneuver
1 Galadriel's Dismissal
1 Giver of Runes
1 Grand Crescendo
1 Guardian Scalelord
1 Guide of Souls
1 Hall of Heliod's Generosity
1 Heliod's Intervention
1 Heraldic Banner
1 Hero of Bladehold
1 Kabira Takedown
1 Leonin Warleader
1 Lightning Greaves
1 Makindi Stampede
1 Mentor of the Meek
1 Mondrak, Glory Dominus
1 Moonshaker Cavalry
1 Mother of Runes
1 Myriad Landscape
1 Nykthos, Shrine to Nyx
1 Path to Exile
1 Pearl Medallion
1 Phyrexian Altar
18 Plains
1 Plaza of Heroes
1 Razorgrass Ambush
1 Reconnaissance
1 Requiem Angel
1 Rogue's Passage
1 Rumor Gatherer
1 Scavenger Grounds
1 Secure the Wastes
1 Sejiri Shelter
1 Selfless Spirit
1 Shadowspear
1 Silverwing Squadron
1 Skullclamp
1 Smothering Tithe
1 Sol Ring
1 Solitude
1 Sword of Feast and Famine
1 Sword of the Animist
1 Swords to Plowshares
1 Teferi's Protection
1 Terrain Generator
1 Tocasia's Welcome
1 Trouble in Pairs
1 War Room
1 Wayfarer's Bauble
1 Welcoming Vampire
1 Witch Enchanter

1 Adeline, Resplendent Cathar
"""


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
        # Synthetic check that digits are not stripped
        assert "2" in _slugify("Card 2 Name")

    def test_hyphen_in_name_preserved(self):
        assert _slugify("K'rrik, Son of Yawgmoth") == "krrik-son-of-yawgmoth"

    def test_no_leading_or_trailing_hyphens(self):
        result = _slugify("Tymna the Weaver")
        assert not result.startswith("-")
        assert not result.endswith("-")


# ── parse_moxfield_txt ────────────────────────────────────────────────────────

class TestParseMoxfieldTxt:
    """Tests for the decklist parser using the real Adeline decklist."""

    # ── Headerless format (commander last after blank line) ───────────────────

    def test_adeline_commander_detected(self):
        commanders, _ = parse_moxfield_txt(_ADELINE_DECKLIST)
        assert commanders == ["Adeline, Resplendent Cathar"]

    def test_adeline_maindeck_total(self):
        # 99 cards in the maindeck (18 Plains + 81 × 1-of)
        _, maindeck = parse_moxfield_txt(_ADELINE_DECKLIST)
        assert len(maindeck) == 99

    def test_adeline_commander_not_in_maindeck(self):
        commanders, maindeck = parse_moxfield_txt(_ADELINE_DECKLIST)
        assert commanders[0] not in maindeck

    def test_adeline_plains_count(self):
        # Multi-quantity line: "18 Plains" must expand to 18 copies
        _, maindeck = parse_moxfield_txt(_ADELINE_DECKLIST)
        assert maindeck.count("Plains") == 18

    def test_adeline_spot_check_cards_present(self):
        _, maindeck = parse_moxfield_txt(_ADELINE_DECKLIST)
        for card in ("Sol Ring", "Smothering Tithe", "Teferi's Protection",
                     "Skullclamp", "Elspeth, Sun's Champion"):
            assert card in maindeck, f"Expected {card!r} in maindeck"

    # ── Sectioned format (Commander / Mainboard headers) ──────────────────────

    def test_sectioned_format(self):
        text = (
            "Commander\n"
            "1 Adeline, Resplendent Cathar\n\n"
            "Mainboard\n"
            "1 Sol Ring\n"
            "1 Swords to Plowshares\n"
        )
        commanders, maindeck = parse_moxfield_txt(text)
        assert commanders == ["Adeline, Resplendent Cathar"]
        assert "Sol Ring" in maindeck
        assert "Swords to Plowshares" in maindeck
        assert len(maindeck) == 2

    def test_sectioned_commander_not_in_maindeck(self):
        text = (
            "Commander\n"
            "1 Adeline, Resplendent Cathar\n\n"
            "Mainboard\n"
            "1 Sol Ring\n"
        )
        commanders, maindeck = parse_moxfield_txt(text)
        assert "Adeline, Resplendent Cathar" not in maindeck

    # ── Set / collector annotation stripping ─────────────────────────────────

    def test_set_annotation_stripped(self):
        text = (
            "Commander\n"
            "1 Adeline, Resplendent Cathar (VOW) 6\n\n"
            "Mainboard\n"
            "1 Sol Ring (C21) 224\n"
        )
        commanders, maindeck = parse_moxfield_txt(text)
        assert commanders == ["Adeline, Resplendent Cathar"]
        assert maindeck == ["Sol Ring"]

    # ── Skip sections ─────────────────────────────────────────────────────────

    def test_sideboard_entries_ignored(self):
        text = (
            "Commander\n"
            "1 Adeline, Resplendent Cathar\n\n"
            "Mainboard\n"
            "1 Sol Ring\n\n"
            "Sideboard\n"
            "1 Plains\n"
        )
        _, maindeck = parse_moxfield_txt(text)
        assert "Plains" not in maindeck
        assert len(maindeck) == 1

    def test_maybeboard_entries_ignored(self):
        text = (
            "Commander\n"
            "1 Adeline, Resplendent Cathar\n\n"
            "Mainboard\n"
            "1 Sol Ring\n\n"
            "Maybeboard\n"
            "1 Anointed Procession\n"
        )
        _, maindeck = parse_moxfield_txt(text)
        assert "Anointed Procession" not in maindeck

    # ── Edge cases ────────────────────────────────────────────────────────────

    def test_empty_input_returns_empty_lists(self):
        commanders, maindeck = parse_moxfield_txt("")
        assert commanders == []
        assert maindeck == []

    def test_commander_not_detected_without_blank_line_separator(self):
        # Without the blank-line separator before the trailing commander,
        # the fallback cannot distinguish commander from maindeck.
        text = (
            "1 Sol Ring\n"
            "1 Adeline, Resplendent Cathar\n"
        )
        commanders, maindeck = parse_moxfield_txt(text)
        assert commanders == []
        assert len(maindeck) == 2

