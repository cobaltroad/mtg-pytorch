"""Tests for functional role detection in synergy/roles.py.

Validates that the ROLE_PATTERNS and LAND_ROLE_PATTERNS correctly identify
functional roles from oracle text, using real MTG oracle text samples.

Acceptance criteria (per the issue):
- Sol Ring → ramp
- Demonic Tutor → tutor
- Cyclonic Rift → removal
- ≥80% recall on a hand-labelled sample of cards per role
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Allow importing from the parent ingest directory without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

from synergy.roles import ROLE_PATTERNS, LAND_ROLE_PATTERNS, is_land_card  # noqa: E402

# Re-use the helper defined in pipeline.py by importing it directly
# (we import from roles and replicate the logic to stay test-independent)
import re


def get_roles(oracle_text: str, type_line: str = "") -> set[str]:
    """Return the set of role names matched for a card."""
    seen: set[str] = set()
    patterns = list(ROLE_PATTERNS)
    if is_land_card(type_line):
        patterns = patterns + list(LAND_ROLE_PATTERNS)
    for pattern, role_name in patterns:
        if role_name in seen:
            continue
        if re.search(pattern, oracle_text, re.IGNORECASE):
            seen.add(role_name)
    return seen


# ── Acceptance-criteria cards ─────────────────────────────────────────────────

class TestAcceptanceCriteria:
    """The three specific cards called out in the issue."""

    def test_sol_ring_is_ramp(self):
        oracle = "{T}: Add {C}{C}."
        assert "ramp" in get_roles(oracle)

    def test_demonic_tutor_is_tutor(self):
        oracle = "Search your library for a card, put that card into your hand, then shuffle."
        assert "tutor" in get_roles(oracle)

    def test_cyclonic_rift_is_removal(self):
        oracle = (
            "Return target nonland permanent you don't control to its owner's hand.\n"
            "Overload {6}{U} (You may cast this spell for its overload cost. If you do, "
            "change its text by replacing all instances of \"target\" with \"each.\")"
        )
        assert "removal" in get_roles(oracle)


# ── Ramp ──────────────────────────────────────────────────────────────────────

RAMP_CARDS = [
    # (name, oracle_text, type_line)
    ("Sol Ring",          "{T}: Add {C}{C}.",                                          "Artifact"),
    ("Llanowar Elves",    "{T}: Add {G}.",                                             "Creature — Elf Druid"),
    ("Cultivate",         "Search your library for up to two basic land cards, reveal those cards, "
                          "put one onto the battlefield tapped and the other into your hand, then shuffle.",
                          "Sorcery"),
    ("Farseek",           "Search your library for a Plains, Island, Swamp, or Mountain card "
                          "and put that card onto the battlefield tapped, then shuffle.",
                          "Sorcery"),
    ("Rampant Growth",    "Search your library for a basic land card, put that card onto the "
                          "battlefield tapped, then shuffle.",                          "Sorcery"),
    ("Nature's Lore",     "Search your library for a Forest card, put that card onto the battlefield, "
                          "then shuffle.",                                              "Sorcery"),
    ("Arcane Signet",     "{T}: Add one mana of any color in your commander's color identity.", "Artifact"),
    ("Exploration",       "You may play an additional land on each of your turns.",    "Enchantment"),
    ("Azusa Lost but Seeking",
                          "You may play two additional lands on each of your turns.",  "Legendary Creature — Human Monk"),
    ("Basalt Monolith",   "{T}: Add {C}{C}{C}.\n{3}: Untap Basalt Monolith.",         "Artifact"),
    ("Selvala Heart of the Wilds",
                          "{T}: Add X mana in any combination of colors, where X is the greatest power "
                          "among creatures you control. Each other player may draw a card.",
                          "Legendary Creature — Elf Scout"),
]

@pytest.mark.parametrize("name,oracle,type_line", RAMP_CARDS)
def test_ramp(name, oracle, type_line):
    roles = get_roles(oracle, type_line)
    assert "ramp" in roles, f"{name!r} should be tagged as ramp; got {roles}"


# ── Draw one-time ─────────────────────────────────────────────────────────────

DRAW_ONE_CARDS = [
    ("Night's Whisper",   "You draw two cards and you lose 2 life.",                  "Sorcery"),
    ("Read the Bones",    "Scry 2, then draw two cards. You lose 2 life.",            "Sorcery"),
    ("Painful Truths",    "You draw three cards and lose 3 life for each color among permanents you control.", "Sorcery"),
    ("Divination",        "Draw two cards.",                                           "Sorcery"),
    ("Brainstorm",        "Draw three cards, then put two cards from your hand on top of your library in any order.", "Instant"),
    ("Impulse",           "Look at the top four cards of your library. Put one of them into your hand "
                          "and the rest on the bottom of your library in any order.",  "Instant"),
    ("Light Up the Stage",
                          "Spectacle {R} (You may cast this spell for its spectacle cost rather than "
                          "its mana cost if an opponent lost life this turn.)\nExile the top two cards "
                          "of your library. Until the end of your next turn, you may play those cards.",
                          "Sorcery"),
    ("Faithless Looting", "Draw two cards, then discard two cards.",                  "Sorcery"),
    ("Gitaxian Probe",    "({U/P} can be paid with either {U} or 2 life.)\nLook at target player's hand.\n"
                          "Draw a card.",                                              "Instant"),
]

@pytest.mark.parametrize("name,oracle,type_line", DRAW_ONE_CARDS)
def test_draw_one(name, oracle, type_line):
    roles = get_roles(oracle, type_line)
    assert "draw_one" in roles, f"{name!r} should be tagged as draw_one; got {roles}"


# ── Draw engine ───────────────────────────────────────────────────────────────

DRAW_ENGINE_CARDS = [
    ("Rhystic Study",
     "Whenever an opponent casts a spell, you may pay {1}. If you don't, draw a card.",
     "Enchantment"),
    ("Phyrexian Arena",
     "At the beginning of your upkeep, you draw a card and you lose 1 life.",
     "Enchantment"),
    ("Consecrated Sphinx",
     "Whenever an opponent draws a card, you may draw two cards.",
     "Creature — Sphinx"),
    ("Howling Mine",
     "At the beginning of each player's draw step, if Howling Mine is untapped, "
     "that player draws an additional card.",
     "Artifact"),
    ("Sylvan Library",
     "At the beginning of your draw step, you may draw two additional cards. If you do, "
     "choose two cards in your hand drawn this turn. For each of those cards, pay 4 life "
     "or put the card on top of your library.",
     "Enchantment"),
    ("Windfall",
     "Each player discards their hand, then draws cards equal to the greatest number of "
     "cards a player discarded this way.",
     "Sorcery"),
    ("Toski Bearer of Secrets",
     "This spell can't be countered.\nIndestructible\nWhenever a creature you control "
     "deals combat damage to a player, draw a card.",
     "Legendary Creature — Squirrel"),
]

@pytest.mark.parametrize("name,oracle,type_line", DRAW_ENGINE_CARDS)
def test_draw_engine(name, oracle, type_line):
    roles = get_roles(oracle, type_line)
    assert "draw_engine" in roles, f"{name!r} should be tagged as draw_engine; got {roles}"


# ── Removal ───────────────────────────────────────────────────────────────────

REMOVAL_CARDS = [
    ("Swords to Plowshares",
     "Exile target creature. Its controller gains life equal to its power.",
     "Instant"),
    ("Path to Exile",
     "Exile target creature. Its controller may search their library for a basic land card, "
     "put that card onto the battlefield tapped, then shuffle.",
     "Instant"),
    ("Doom Blade",         "Destroy target nonblack creature.",                        "Instant"),
    ("Generous Gift",      "Destroy target permanent. Its controller creates a 3/3 green Elephant creature token.", "Instant"),
    ("Lightning Bolt",     "Lightning Bolt deals 3 damage to any target.",             "Instant"),
    ("Cyclonic Rift",
     "Return target nonland permanent you don't control to its owner's hand.\n"
     "Overload {6}{U}",
     "Instant"),
    ("Krosan Grip",        "Split second (As long as this spell is on the stack, players can't cast "
                           "spells or activate abilities that aren't mana abilities.)\n"
                           "Destroy target artifact or enchantment.",
                           "Instant"),
    ("Chaos Warp",         "The owner of target permanent shuffles it into their library, then reveals "
                           "the top card of their library. If it's a permanent card, they put it onto "
                           "the battlefield.",
                           "Instant"),
    ("Rapid Hybridization",
     "Destroy target creature. That creature's controller creates a 3/3 green Frog Lizard creature token.",
     "Instant"),
    ("Unsummon",           "Return target creature to its owner's hand.",              "Instant"),
]

@pytest.mark.parametrize("name,oracle,type_line", REMOVAL_CARDS)
def test_removal(name, oracle, type_line):
    roles = get_roles(oracle, type_line)
    assert "removal" in roles, f"{name!r} should be tagged as removal; got {roles}"


# ── Sweeper ───────────────────────────────────────────────────────────────────

SWEEPER_CARDS = [
    ("Wrath of God",       "Destroy all creatures. They can't be regenerated.",       "Sorcery"),
    ("Damnation",          "Destroy all creatures. They can't be regenerated.",       "Sorcery"),
    ("Blasphemous Act",
     "This spell costs {1} less to cast for each creature on the battlefield.\n"
     "Blasphemous Act deals 13 damage to each creature.",
     "Sorcery"),
    ("Toxic Deluge",
     "As an additional cost to cast this spell, pay X life.\n"
     "All creatures get -X/-X until end of turn.",
     "Sorcery"),
    ("Evacuation",         "Return all creatures to their owners' hands.",             "Instant"),
    ("In Garruk's Wake",
     "Destroy all creatures you don't control and all planeswalkers you don't control.",
     "Sorcery"),
    ("Cyclonic Rift Overload",
     "Return target nonland permanent you don't control to its owner's hand.\n"
     "Overload {6}{U} (You may cast this spell for its overload cost. If you do, "
     "change its text by replacing all instances of \"target\" with \"each.\")",
     "Instant"),
]

@pytest.mark.parametrize("name,oracle,type_line", SWEEPER_CARDS)
def test_sweeper(name, oracle, type_line):
    roles = get_roles(oracle, type_line)
    assert "sweeper" in roles, f"{name!r} should be tagged as sweeper; got {roles}"


# ── Tutor ─────────────────────────────────────────────────────────────────────

TUTOR_CARDS = [
    ("Demonic Tutor",
     "Search your library for a card, put that card into your hand, then shuffle.",
     "Sorcery"),
    ("Vampiric Tutor",
     "Search your library for a card, then shuffle and put that card on top.",
     "Instant"),
    ("Eladamri's Call",
     "Search your library for a creature card, reveal it, put it into your hand, then shuffle.",
     "Instant"),
    ("Fabricate",
     "Search your library for an artifact card, reveal it, put it into your hand, then shuffle.",
     "Sorcery"),
    ("Finale of Devastation",
     "Search your library and/or graveyard for a creature card with mana value X or less "
     "and put it onto the battlefield. If you searched your library this way, shuffle. "
     "If X is 10 or more, creatures you control get +X/+X and gain haste until end of turn.",
     "Sorcery"),
    ("Worldly Tutor",
     "Search your library for a creature card and reveal that card. Shuffle, then put the card on top.",
     "Instant"),
    ("Imperial Seal",
     "Search your library for a card, then shuffle and put that card on top of your library. "
     "You lose 2 life.",
     "Sorcery"),
    ("Chord of Calling",
     "Convoke (Your creatures can help cast this spell. Each creature you tap while casting this "
     "spell pays for {1} or one mana of that creature's color.)\n"
     "Search your library for a creature card with mana value X or less, put it onto the "
     "battlefield, then shuffle.",
     "Instant"),
]

@pytest.mark.parametrize("name,oracle,type_line", TUTOR_CARDS)
def test_tutor(name, oracle, type_line):
    roles = get_roles(oracle, type_line)
    assert "tutor" in roles, f"{name!r} should be tagged as tutor; got {roles}"


# ── Protection ────────────────────────────────────────────────────────────────

PROTECTION_CARDS = [
    ("Darksteel Plate",
     "Indestructible\nEquipped creature is indestructible.\nEquip {2}",
     "Artifact — Equipment"),
    ("Lightning Greaves",
     "Equipped creature has haste and shroud. (It can't be the target of spells or abilities.)\n"
     "Equip {0}",
     "Artifact — Equipment"),
    ("Swiftfoot Boots",
     "Equipped creature has haste and hexproof.\nEquip {1}",
     "Artifact — Equipment"),
    ("Privileged Position",
     "Other permanents you control have hexproof.",
     "Enchantment"),
    ("Gift of Immortality",
     "Enchant creature\nEnchanted creature has indestructible.\n"
     "When enchanted creature dies, return it to the battlefield under its owner's control, "
     "then return Gift of Immortality to the battlefield attached to that creature.",
     "Enchantment — Aura"),
]

@pytest.mark.parametrize("name,oracle,type_line", PROTECTION_CARDS)
def test_protection(name, oracle, type_line):
    roles = get_roles(oracle, type_line)
    assert "protection" in roles, f"{name!r} should be tagged as protection; got {roles}"


# ── Win condition ─────────────────────────────────────────────────────────────

WIN_CON_CARDS = [
    ("Blightsteel Colossus",
     "Trample, infect\nIf Blightsteel Colossus would be put into a graveyard from anywhere, "
     "reveal Blightsteel Colossus and shuffle it into its owner's library instead.",
     "Artifact Creature — Golem"),
    ("Thassa's Oracle",
     "When Thassa's Oracle enters the battlefield, look at the top X cards of your library, "
     "where X is your devotion to blue. Put up to one of them on top and the rest on the bottom "
     "in any order. If X is greater than or equal to the number of cards in your library, "
     "you win the game.",
     "Creature — God"),
    ("Laboratory Maniac",
     "If you would draw a card while your library has no cards in it, you win the game instead.",
     "Creature — Human Wizard"),
    ("Phyrexian Crusader",
     "First strike, protection from red and from white, infect",
     "Creature — Zombie Knight"),
    # Approach of the Second Sun: explicit "you win the game" text
    ("Approach of the Second Sun",
     "If Approach of the Second Sun was cast from your hand and you've cast another spell "
     "named Approach of the Second Sun this game, you win the game. Otherwise, put Approach "
     "of the Second Sun into its owner's library seventh from the top and you gain 7 life.",
     "Sorcery"),
]

@pytest.mark.parametrize("name,oracle,type_line", WIN_CON_CARDS)
def test_win_condition(name, oracle, type_line):
    roles = get_roles(oracle, type_line)
    assert "win_condition" in roles, f"{name!r} should be tagged as win_condition; got {roles}"


# ── Anthem ────────────────────────────────────────────────────────────────────

ANTHEM_CARDS = [
    ("Glorious Anthem",    "Creatures you control get +1/+1.",                         "Enchantment"),
    ("Dictate of Heliod",  "Flash\nCreatures you control get +2/+2.",                 "Enchantment"),
    ("Intangible Virtue",  "Creature tokens you control get +1/+1 and have vigilance.", "Enchantment"),
    ("Coat of Arms",
     "Each creature gets +1/+1 for each other creature on the battlefield that shares "
     "a creature type with it.",
     "Artifact"),
    ("Shared Animosity",
     "Whenever a creature you control attacks, it gets +1/+0 until end of turn for each "
     "other attacking creature that shares a creature type with it.",
     "Enchantment"),
]

@pytest.mark.parametrize("name,oracle,type_line", ANTHEM_CARDS)
def test_anthem(name, oracle, type_line):
    roles = get_roles(oracle, type_line)
    assert "anthem" in roles, f"{name!r} should be tagged as anthem; got {roles}"


# ── Token generator ───────────────────────────────────────────────────────────

TOKEN_GEN_CARDS = [
    ("Grave Titan",
     "Deathtouch\nWhenever Grave Titan enters the battlefield or attacks, create two 2/2 "
     "black Zombie creature tokens.",
     "Creature — Giant"),
    ("Young Pyromancer",
     "Whenever you cast an instant or sorcery spell, create a 1/1 red Elemental creature token.",
     "Creature — Human Shaman"),
    ("Avenger of Zendikar",
     "When Avenger of Zendikar enters the battlefield, create a 0/1 green Plant creature token "
     "for each land you control.\nWhenever a land enters the battlefield under your control, "
     "put a +1/+1 counter on each Plant you control.",
     "Creature — Elemental"),
    ("Thopter Foundry",
     "{1}, Sacrifice an artifact: Create a 1/1 colorless Thopter artifact creature token with "
     "flying. You gain 1 life.",
     "Artifact"),
    ("Secure the Wastes",
     "Create X 1/1 white Warrior creature tokens.",
     "Instant"),
]

@pytest.mark.parametrize("name,oracle,type_line", TOKEN_GEN_CARDS)
def test_token_generator(name, oracle, type_line):
    roles = get_roles(oracle, type_line)
    assert "token_generator" in roles, f"{name!r} should be tagged as token_generator; got {roles}"


# ── Recursion ─────────────────────────────────────────────────────────────────

RECURSION_CARDS = [
    ("Eternal Witness",
     "When Eternal Witness enters the battlefield, you may return target card from your "
     "graveyard to your hand.",
     "Creature — Human Shaman"),
    ("Reanimate",
     "Put target creature card from a graveyard onto the battlefield under your control. "
     "You lose life equal to its mana value.",
     "Sorcery"),
    ("Sheoldred Whispering One",
     "Swampwalk\nAt the beginning of your upkeep, return target creature card from your "
     "graveyard to the battlefield.\nAt the beginning of each opponent's upkeep, that "
     "player sacrifices a creature.",
     "Legendary Creature — Praetor"),
    ("Animate Dead",
     "Enchant creature card in a graveyard\nWhen Animate Dead enters the battlefield, if "
     "it's on the battlefield, it loses \"enchant creature card in a graveyard\" and gains "
     "\"enchant creature put onto the battlefield with Animate Dead.\" Return enchanted "
     "creature card to the battlefield under your control and attach Animate Dead to it.",
     "Enchantment — Aura"),
    ("Phyrexian Reclamation",
     "{1}{B}, Pay 2 life: Return target creature card from your graveyard to your hand.",
     "Enchantment"),
]

@pytest.mark.parametrize("name,oracle,type_line", RECURSION_CARDS)
def test_recursion(name, oracle, type_line):
    roles = get_roles(oracle, type_line)
    assert "recursion" in roles, f"{name!r} should be tagged as recursion; got {roles}"


# ── Interaction (counterspells) ───────────────────────────────────────────────

INTERACTION_CARDS = [
    ("Counterspell",       "Counter target spell.",                                    "Instant"),
    ("Force of Will",
     "You may pay 1 life and exile a blue card from your hand rather than pay this spell's "
     "mana cost.\nCounter target spell.",
     "Instant"),
    ("Negate",             "Counter target noncreature spell.",                        "Instant"),
    ("Swan Song",
     "Counter target enchantment, instant, or sorcery spell. Its controller creates a "
     "2/2 blue Bird creature token with flying.",
     "Instant"),
    ("Mana Drain",
     "Counter target spell. At the beginning of your next main phase, add an amount of "
     "{C} equal to that spell's mana value.",
     "Instant"),
    ("Deflecting Swat",
     "If you control a commander, you may cast this spell without paying its mana cost.\n"
     "Change the target of target spell or ability with a single target.",
     "Instant"),
]

@pytest.mark.parametrize("name,oracle,type_line", INTERACTION_CARDS)
def test_interaction(name, oracle, type_line):
    roles = get_roles(oracle, type_line)
    assert "interaction" in roles, f"{name!r} should be tagged as interaction; got {roles}"


# ── Combat trick ──────────────────────────────────────────────────────────────

COMBAT_TRICK_CARDS = [
    ("Giant Growth",       "Target creature gets +3/+3 until end of turn.",            "Instant"),
    ("Berserk",
     "Cast this spell only before the combat damage step.\nTarget creature gains trample "
     "and gets +X/+0 until end of turn, where X is its power. At the beginning of the next "
     "end step, destroy that creature if it attacked this turn.",
     "Instant"),
    ("Slip Through Space",
     "Target creature can't be blocked this turn.\nDraw a card.",
     "Instant"),
    ("Temur Battle Rage",
     "Target creature gains double strike until end of turn.\nFerocious — That creature also "
     "gains trample until end of turn if you control a creature with power 4 or greater.",
     "Instant"),
]

@pytest.mark.parametrize("name,oracle,type_line", COMBAT_TRICK_CARDS)
def test_combat_trick(name, oracle, type_line):
    roles = get_roles(oracle, type_line)
    assert "combat_trick" in roles, f"{name!r} should be tagged as combat_trick; got {roles}"


# ── Mana land / utility land ──────────────────────────────────────────────────

MANA_LAND_CARDS = [
    ("Forest",      "{T}: Add {G}.",              "Basic Land — Forest"),
    ("Island",      "{T}: Add {U}.",              "Basic Land — Island"),
    ("Command Tower",
     "{T}: Add one mana of any color in your commander's color identity.",
     "Land"),
    ("Cabal Coffers",
     "{2}, {T}: Add {B} for each Swamp you control.",
     "Land"),
    # Exotic Orchard is a mana land with {T}: Add {one mana of any color}
    ("Exotic Orchard",
     "{T}: Add one mana of any color that a land an opponent controls could produce.",
     "Land"),
]

@pytest.mark.parametrize("name,oracle,type_line", MANA_LAND_CARDS)
def test_mana_land(name, oracle, type_line):
    roles = get_roles(oracle, type_line)
    assert "mana_land" in roles, f"{name!r} should be tagged as mana_land; got {roles}"


UTILITY_LAND_CARDS = [
    ("Verdant Catacombs",
     "{T}, Pay 1 life, Sacrifice Verdant Catacombs: Search your library for a Swamp or Forest "
     "card, put it onto the battlefield, then shuffle.",
     "Land"),
    ("Windswept Heath",
     "{T}, Pay 1 life, Sacrifice Windswept Heath: Search your library for a Plains or Forest "
     "card, put it onto the battlefield, then shuffle.",
     "Land"),
    ("Prismatic Vista",
     "{T}, Pay 1 life, Sacrifice Prismatic Vista: Search your library for a basic land card, "
     "put it onto the battlefield, then shuffle.",
     "Land"),
]

@pytest.mark.parametrize("name,oracle,type_line", UTILITY_LAND_CARDS)
def test_utility_land(name, oracle, type_line):
    roles = get_roles(oracle, type_line)
    assert "utility_land" in roles, f"{name!r} should be tagged as utility_land; got {roles}"


# ── Recall rate checks ────────────────────────────────────────────────────────

def _recall(cards, role):
    hits = sum(1 for _, oracle, tl in cards if role in get_roles(oracle, tl))
    return hits / len(cards)


def test_recall_all_roles():
    """Verify ≥80% recall across all labelled samples for each role."""
    role_samples = {
        "ramp":            RAMP_CARDS,
        "draw_one":        DRAW_ONE_CARDS,
        "draw_engine":     DRAW_ENGINE_CARDS,
        "removal":         REMOVAL_CARDS,
        "sweeper":         SWEEPER_CARDS,
        "tutor":           TUTOR_CARDS,
        "protection":      PROTECTION_CARDS,
        "win_condition":   WIN_CON_CARDS,
        "anthem":          ANTHEM_CARDS,
        "token_generator": TOKEN_GEN_CARDS,
        "recursion":       RECURSION_CARDS,
        "interaction":     INTERACTION_CARDS,
        "combat_trick":    COMBAT_TRICK_CARDS,
    }
    failures = []
    for role, cards in role_samples.items():
        r = _recall(cards, role)
        if r < 0.80:
            failures.append(f"{role}: {r:.0%} ({int(r * len(cards))}/{len(cards)})")

    assert not failures, "Recall < 80% for:\n" + "\n".join(failures)
