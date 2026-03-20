"""Tests for compute_tribal_typeline_synergy commander-qualification logic.

The key invariant under test: a commander should be paired with tribe members
only if the tribe name appears in the commander's oracle text.  Matching purely
on type_line caused false positives (e.g. every Legendary Human being paired
with all Humans regardless of whether the card has any Human-matters text).

The SQL queries in pipeline.py are translated into equivalent Python predicates
so we can test them without a database connection.

Commander qualification (the fixed query):
    lower(type_line) LIKE '%creature%'
    AND lower(type_line) LIKE '%legendary%'
    AND lower(oracle_text) LIKE '%{tribe}%'

Member qualification (unchanged):
    (lower(type_line) LIKE '%{tribe}%' AND lower(type_line) LIKE '%creature%')
    OR is_changeling(card)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from synergy.tribal import TRIBES  # noqa: E402


# ── Python equivalents of the pipeline SQL predicates ────────────────────────

def is_member(tribe: str, type_line: str, oracle_text: str, keywords: list[str]) -> bool:
    """Mirror of the all_members SQL query in compute_tribal_typeline_synergy."""
    t = tribe.lower()
    is_changeling = (
        "Changeling" in keywords
        or "is every creature type" in oracle_text.lower()
    )
    return (
        (t in type_line.lower() and "creature" in type_line.lower())
        or is_changeling
    )


def is_commander(tribe: str, type_line: str, oracle_text: str) -> bool:
    """Mirror of the fixed commanders SQL query in compute_tribal_typeline_synergy.

    Requires the tribe name to appear in oracle_text (not just type_line).
    """
    t = tribe.lower()
    return (
        "creature" in type_line.lower()
        and "legendary" in type_line.lower()
        and t in oracle_text.lower()
    )


def was_buggy_commander(tribe: str, type_line: str) -> bool:
    """The old (broken) commander query — matched on type_line only."""
    t = tribe.lower()
    return (
        t in type_line.lower()
        and "creature" in type_line.lower()
        and "legendary" in type_line.lower()
    )


# ── Fixture data ──────────────────────────────────────────────────────────────
#
# Each card is (name, type_line, oracle_text, keywords[])

# True tribal commanders: mention the tribe in oracle text.
REAL_TRIBAL_COMMANDERS = [
    ("Wilhelt, the Rotcleaver", "Legendary Creature — Zombie Warrior",
     "Whenever another nontoken Zombie you control dies, if it didn't have decayed, "
     "create a 2/2 black Zombie creature token with decayed.\n"
     "At the beginning of your end step, you may sacrifice a Zombie. If you do, draw a card.",
     []),
    ("The Scarab God", "Legendary Creature — God",
     "At the beginning of your upkeep, each opponent loses X life and you scry X, "
     "where X is the number of Zombies you control.\n"
     "{2}{U}{B}: Exile target creature card from a graveyard. Create a token that's a copy "
     "of it, except it's a 4/4 black Zombie.",
     []),
    ("Voja, Jaws of the Conclave", "Legendary Creature — Wolf",
     "Whenever Voja, Jaws of the Conclave attacks, for each Elf that entered the battlefield "
     "under your control this turn, put a +1/+1 counter on each Wolf you control. "
     "Draw a card for each Wolf that entered the battlefield under your control this turn.",
     []),
    ("Hakbal of the Surging Soul", "Legendary Creature — Merfolk Scout",
     "Whenever a Merfolk you control explores, you may put a +1/+1 counter on it.\n"
     "At the beginning of your end step, the Merfolk you control with the most +1/+1 counters "
     "on it explores.",
     []),
    ("Tiamat", "Legendary Creature — Dragon God",
     "Flying\nWhen Tiamat enters the battlefield, if you cast it, search your library for up "
     "to five Dragon cards not named Tiamat that each have different names, reveal them, "
     "put them into your hand, then shuffle.",
     []),
    ("Lathril, Blade of the Elves", "Legendary Creature — Elf Noble",
     "Menace\nWhenever Lathril, Blade of the Elves deals combat damage to a player, "
     "create that many 1/1 green Elf Warrior creature tokens.\n"
     "{T}, Tap ten untapped Elves you control: Each opponent loses 10 life and you gain 10 life.",
     []),
    ("Edgar Markov", "Legendary Creature — Vampire Knight",
     "Eminence — Whenever you cast another Vampire spell, if Edgar Markov is in the command "
     "zone or on the battlefield, create a 1/1 black Vampire creature token.\n"
     "First strike, haste\nWhenever Edgar Markov attacks, put a +1/+1 counter on each Vampire "
     "you control.",
     []),
]

# False-positive commanders: Legendary creatures whose type_line contains the
# tribe name but whose oracle text has no tribal-matters text.
FALSE_POSITIVE_COMMANDERS = [
    # Generic Legendary Humans with no "Human" in oracle text — the canonical bug case.
    ("Kenrith, the Returned King", "Legendary Creature — Human Noble",
     "{R}: Target player puts a +1/+1 counter on each creature they control.\n"
     "{G}: Target player puts the top two cards of their library into their graveyard.\n"
     "{W}: Target player gains 5 life.\n"
     "{U}: Target player draws a card.\n"
     "{B}: Return target creature card from your graveyard to the battlefield.",
     []),
    ("Thrasios, Triton Hero", "Legendary Creature — Merfolk Wizard",
     "{4}: Scry 1, then reveal the top card of your library. If it's a land card, put it "
     "onto the battlefield tapped. Otherwise, draw a card.",
     []),
    ("Kraum, Ludevic's Opus", "Legendary Creature — Zombie Horror",
     "Flying, haste\nWhenever an opponent casts their second spell each turn, draw a card.\n"
     "Partner (You can have two commanders if both have partner.)",
     []),
    ("Atraxa, Praetors' Voice", "Legendary Creature — Phyrexian Angel Horror",
     "Flying, vigilance, deathtouch, lifelink\nAt the beginning of your end step, proliferate.",
     []),
    ("Sisay, Weatherlight Captain", "Legendary Creature — Human Soldier",
     "{X}: Search your library for a legendary permanent card with mana value X or less, "
     "put it onto the battlefield, then shuffle.",
     []),
    # Ayara is an Elf Noble but her oracle text references "black creature", not "Elf"
    ("Ayara, First of Locthwain", "Legendary Creature — Elf Noble",
     "Whenever Ayara, First of Locthwain or another black creature enters the battlefield "
     "under your control, each opponent loses 1 life and you gain 1 life.\n"
     "{T}, Sacrifice another black creature: Draw a card.",
     []),
    # Atraxa is an Angel but oracle text never says "Angel"
    # Kraum is a Zombie but oracle text never says "Zombie"
    # Thrasios is a Merfolk but oracle text never says "Merfolk"
]

# Cards that ARE tribe members (type_line contains the tribe type).
TRIBE_MEMBERS = [
    ("Gravecrawler",    "Creature — Zombie",       "Zombie", []),
    ("Llanowar Elves",  "Creature — Elf Druid",    "Elf",    []),
    ("Thunderbreak Regent", "Creature — Dragon",   "Dragon", []),
    ("Master of the Pearl Trident", "Creature — Merfolk", "Merfolk", []),
    ("Lightning-Rig Crew", "Creature — Goblin Pirate", "Goblin", []),
    ("Lightning-Rig Crew", "Creature — Goblin Pirate", "Pirate", []),
]

# Changelings must qualify as members of every tribe.
CHANGELINGS = [
    ("Mothdust Changeling", "Creature — Shapeshifter", "", ["Changeling"]),
    ("Mirror Entity",       "Creature — Shapeshifter", "", ["Changeling"]),
    ("Universal Automaton", "Artifact Creature — Shapeshifter",
     "Universal Automaton is every creature type.", []),
]

# Non-creature cards and non-Legendary cards must never qualify as commanders.
NOT_COMMANDERS = [
    ("Gravecrawler",    "Creature — Zombie",
     "You may cast Gravecrawler from your graveyard as long as you control a Zombie.",
     []),
    ("Zombie Apocalypse", "Sorcery",
     "Return all Zombie creature cards from your graveyard to the battlefield tapped, "
     "then destroy all Humans.",
     []),
    ("Coat of Arms", "Artifact",
     "Each creature gets +1/+1 for each other creature on the battlefield that shares "
     "a creature type with it.",
     []),
]


# ── Tests: commander qualification ───────────────────────────────────────────

class TestCommanderQualification:
    """is_commander must require the tribe name in oracle_text."""

    @pytest.mark.parametrize("name,type_line,oracle,keywords", REAL_TRIBAL_COMMANDERS)
    def test_real_tribal_commanders_qualify(self, name, type_line, oracle, keywords):
        """Commanders that mention the tribe in oracle text must qualify."""
        # Determine which tribe(s) to test against based on oracle text content.
        matched = [t for t in TRIBES if t.lower() in oracle.lower()]
        assert matched, (
            f"{name!r}: test data error — no TRIBES entry found in oracle text"
        )
        for tribe in matched:
            assert is_commander(tribe, type_line, oracle), (
                f"{name!r} should qualify as a {tribe} commander "
                f"(tribe name appears in oracle text)"
            )

    @pytest.mark.parametrize("name,type_line,oracle,keywords", FALSE_POSITIVE_COMMANDERS)
    def test_false_positive_commanders_excluded(self, name, type_line, oracle, keywords):
        """Commanders whose type_line matches the tribe but oracle text does not
        must NOT qualify — this is the core bug that was fixed."""
        # For each tribe in their type_line that does NOT appear in oracle text,
        # assert is_commander returns False.
        for tribe in TRIBES:
            t = tribe.lower()
            in_typeline = t in type_line.lower()
            in_oracle   = t in oracle.lower()
            if in_typeline and not in_oracle:
                assert not is_commander(tribe, type_line, oracle), (
                    f"{name!r}: tribe '{tribe}' is in type_line but NOT oracle text; "
                    f"is_commander should return False (would have been True with old bug)"
                )

    @pytest.mark.parametrize("name,type_line,oracle,keywords", FALSE_POSITIVE_COMMANDERS)
    def test_old_bug_would_have_matched(self, name, type_line, oracle, keywords):
        """Confirm the old type_line-only query WOULD have matched these cards,
        proving the fix is necessary."""
        any_false_positive = any(
            was_buggy_commander(tribe, type_line)
            and tribe.lower() not in oracle.lower()
            for tribe in TRIBES
        )
        assert any_false_positive, (
            f"{name!r}: expected the old query to produce at least one false-positive "
            f"match; verify this card is a good test case"
        )

    @pytest.mark.parametrize("name,type_line,oracle,keywords", NOT_COMMANDERS)
    def test_non_legendary_non_creature_never_qualify(self, name, type_line, oracle, keywords):
        """Non-Legendary cards and non-creature cards must never qualify as commanders."""
        for tribe in TRIBES:
            assert not is_commander(tribe, type_line, oracle), (
                f"{name!r} (type_line={type_line!r}) should never qualify as a commander"
            )


# ── Tests: member qualification ───────────────────────────────────────────────

class TestMemberQualification:
    """is_member is based on type_line — this behaviour is correct and unchanged."""

    @pytest.mark.parametrize("name,type_line,tribe,keywords", TRIBE_MEMBERS)
    def test_tribe_members_qualify(self, name, type_line, tribe, keywords):
        assert is_member(tribe, type_line, "", keywords), (
            f"{name!r} should qualify as a {tribe} member via type_line"
        )

    @pytest.mark.parametrize("name,type_line,oracle,keywords", CHANGELINGS)
    def test_changelings_qualify_for_all_tribes(self, name, type_line, oracle, keywords):
        """Changelings must qualify as members of every tribe."""
        for tribe in TRIBES:
            assert is_member(tribe, type_line, oracle, keywords), (
                f"Changeling {name!r} should qualify as a {tribe} member"
            )

    def test_non_creature_not_a_member(self):
        """Spells are never tribe members regardless of oracle text."""
        assert not is_member("Zombie", "Sorcery", "Return all Zombie cards.", [])
        assert not is_member("Elf", "Enchantment", "Elves you control gain +1/+1.", [])


# ── Tests: fix does not regress legitimate commanders ─────────────────────────

class TestNoRegression:
    """Spot-check that specific well-known tribal commanders still qualify
    for the correct tribe after the oracle-text requirement was added."""

    CASES = [
        # (commander_name, tribe, type_line, oracle_text)
        ("Wilhelt, the Rotcleaver", "Zombie",
         "Legendary Creature — Zombie Warrior",
         "Whenever another nontoken Zombie you control dies, if it didn't have decayed, "
         "create a 2/2 black Zombie creature token with decayed.\n"
         "At the beginning of your end step, you may sacrifice a Zombie. If you do, draw a card."),
        ("The Scarab God", "Zombie",
         "Legendary Creature — God",
         "At the beginning of your upkeep, each opponent loses X life and you scry X, "
         "where X is the number of Zombies you control.\n"
         "{2}{U}{B}: Exile target creature card from a graveyard. Create a token that's a copy "
         "of it, except it's a 4/4 black Zombie."),
        ("Voja, Jaws of the Conclave", "Wolf",
         "Legendary Creature — Wolf",
         "Whenever Voja, Jaws of the Conclave attacks, for each Elf that entered the battlefield "
         "under your control this turn, put a +1/+1 counter on each Wolf you control. "
         "Draw a card for each Wolf that entered the battlefield under your control this turn."),
        ("Voja, Jaws of the Conclave", "Elf",
         "Legendary Creature — Wolf",
         "Whenever Voja, Jaws of the Conclave attacks, for each Elf that entered the battlefield "
         "under your control this turn, put a +1/+1 counter on each Wolf you control. "
         "Draw a card for each Wolf that entered the battlefield under your control this turn."),
        ("Lathril, Blade of the Elves", "Elf",
         "Legendary Creature — Elf Noble",
         "Menace\nWhenever Lathril, Blade of the Elves deals combat damage to a player, "
         "create that many 1/1 green Elf Warrior creature tokens.\n"
         "{T}, Tap ten untapped Elves you control: Each opponent loses 10 life and you gain 10 life."),
        ("Tiamat", "Dragon",
         "Legendary Creature — Dragon God",
         "Flying\nWhen Tiamat enters the battlefield, if you cast it, search your library for up "
         "to five Dragon cards not named Tiamat that each have different names, reveal them, "
         "put them into your hand, then shuffle."),
        ("Hakbal of the Surging Soul", "Merfolk",
         "Legendary Creature — Merfolk Scout",
         "Whenever a Merfolk you control explores, you may put a +1/+1 counter on it.\n"
         "At the beginning of your end step, the Merfolk you control with the most +1/+1 counters "
         "on it explores."),
        ("Edgar Markov", "Vampire",
         "Legendary Creature — Vampire Knight",
         "Eminence — Whenever you cast another Vampire spell, if Edgar Markov is in the command "
         "zone or on the battlefield, create a 1/1 black Vampire creature token.\n"
         "First strike, haste\nWhenever Edgar Markov attacks, put a +1/+1 counter on each Vampire "
         "you control."),
    ]

    @pytest.mark.parametrize("name,tribe,type_line,oracle", CASES)
    def test_commander_still_qualifies(self, name, tribe, type_line, oracle):
        assert is_commander(tribe, type_line, oracle), (
            f"{name!r} should still qualify as a {tribe} commander after the fix"
        )

    FALSE_POSITIVE_CASES = [
        # (commander_name, tribe_that_must_NOT_match, type_line, oracle_text)
        # Kenrith is a Human but has no Human-matters text
        ("Kenrith, the Returned King", "Human",
         "Legendary Creature — Human Noble",
         "{R}: Target player puts a +1/+1 counter on each creature they control.\n"
         "{G}: Target player puts the top two cards of their library into their graveyard.\n"
         "{W}: Target player gains 5 life.\n"
         "{U}: Target player draws a card.\n"
         "{B}: Return target creature card from your graveyard to the battlefield."),
        # Sisay is a Human Soldier but oracle text contains no tribal Human text
        ("Sisay, Weatherlight Captain", "Human",
         "Legendary Creature — Human Soldier",
         "{X}: Search your library for a legendary permanent card with mana value X or less, "
         "put it onto the battlefield, then shuffle."),
        # Thrasios is a Merfolk but oracle text never references Merfolk
        ("Thrasios, Triton Hero", "Merfolk",
         "Legendary Creature — Merfolk Wizard",
         "{4}: Scry 1, then reveal the top card of your library. If it's a land card, put it "
         "onto the battlefield tapped. Otherwise, draw a card."),
        # Kraum is a Zombie but oracle text never references Zombie
        ("Kraum, Ludevic's Opus", "Zombie",
         "Legendary Creature — Zombie Horror",
         "Flying, haste\nWhenever an opponent casts their second spell each turn, draw a card.\n"
         "Partner (You can have two commanders if both have partner.)"),
    ]

    @pytest.mark.parametrize("name,tribe,type_line,oracle", FALSE_POSITIVE_CASES)
    def test_false_positives_excluded(self, name, tribe, type_line, oracle):
        assert not is_commander(tribe, type_line, oracle), (
            f"{name!r} must NOT qualify as a {tribe} commander — "
            f"tribe name is absent from oracle text"
        )
