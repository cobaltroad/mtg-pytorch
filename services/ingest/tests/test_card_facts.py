"""Golden tests for shared/composition/card_facts.py (plan W1).

Oracle texts are hardcoded (current MTGJSON templating unless noted) so the
tests are pure — no DB, no network.  Covers the mana-symbol zoo (hybrid,
monocolor hybrid, phyrexian, {C}, snow, X, no-cost) and the land cycles the
mana-base solver must distinguish (shock/check/fast/tango = conditional,
temple/bounce = always, fetches, MDFCs).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Host runs: add repo/shared to the path.  In-container runs already see the
# package via PYTHONPATH=/shared (where /app is the ingest service root).
for _parent in Path(__file__).resolve().parents:
    if (_parent / "shared" / "composition").is_dir():
        sys.path.insert(0, str(_parent / "shared"))
        break

from composition.card_facts import (  # noqa: E402
    classify_land,
    compute_card_facts,
    is_mdfc_land,
    parse_mana_cost,
)

# ── Mana cost parsing ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "cost", "generic", "has_x", "pips", "hybrid"),
    [
        ("Lightning Bolt", "{R}", 0, False, {"R": 1}, []),
        ("Counterspell", "{U}{U}", 0, False, {"U": 2}, []),
        ("Sol Ring", "{1}", 1, False, {}, []),
        ("Emrakul, the Aeons Torn", "{15}", 15, False, {}, []),
        ("Fireball", "{X}{R}", 0, True, {"R": 1}, []),
        ("Crackle with Power", "{X}{X}{R}{R}{R}", 0, True, {"R": 3}, []),
        ("Boros Charm", "{R}{W}", 0, False, {"R": 1, "W": 1}, []),
        ("Nicol Bolas, Dragon-God", "{U}{B}{B}{R}", 0, False, {"U": 1, "B": 2, "R": 1}, []),
        ("Atraxa, Praetors' Voice", "{G}{W}{U}{B}", 0, False, {"G": 1, "W": 1, "U": 1, "B": 1}, []),
        # Hybrid
        ("Boros Reckoner", "{R/W}{R/W}{R/W}", 0, False, {}, [["R", "W"]] * 3),
        # Monocolor hybrid
        ("Spectral Procession", "{2/W}{2/W}{2/W}", 0, False, {}, [["2", "W"]] * 3),
        (
            "Reaper King",
            "{2/W}{2/U}{2/B}{2/R}{2/G}",
            0,
            False,
            {},
            [["2", "W"], ["2", "U"], ["2", "B"], ["2", "R"], ["2", "G"]],
        ),
        # Phyrexian
        ("Dismember", "{1}{B/P}{B/P}", 1, False, {}, [["B", "P"]] * 2),
        ("Gitaxian Probe", "{U/P}", 0, False, {}, [["U", "P"]]),
        # Hybrid phyrexian
        ("Ajani, Sleeper Agent", "{1}{G}{G/W/P}{W}", 1, False, {"G": 1, "W": 1}, [["G", "W", "P"]]),
        # Colorless-required and snow pips
        ("Kozilek, the Great Distortion", "{8}{C}{C}", 8, False, {"C": 2}, []),
        ("Icehide Golem", "{S}", 0, False, {"S": 1}, []),
        # No mana cost
        ("Ancestral Vision", None, 0, False, {}, []),
        ("Living End", "", 0, False, {}, []),
    ],
)
def test_parse_mana_cost(name, cost, generic, has_x, pips, hybrid):
    p = parse_mana_cost(cost)
    assert p.generic == generic, name
    assert p.has_x is has_x, name
    assert p.pips == pips, name
    assert p.hybrid == hybrid, name


# ── Land classification ───────────────────────────────────────────────────────

_LANDS = [
    # (name, type_line, oracle_text, produced_mana,
    #  is_land, is_basic, colors, etb_tapped, is_fetch)
    (
        "Command Tower",
        "Land",
        "{T}: Add one mana of any color in your commander's color identity.",
        ["B", "G", "R", "U", "W"],
        True, False, ["B", "G", "R", "U", "W"], "untapped", False,
    ),
    (
        "Plains",
        "Basic Land — Plains",
        "({T}: Add {W}.)",
        ["W"],
        True, True, ["W"], "untapped", False,
    ),
    (
        "Temple of Silence",
        "Land",
        "This land enters tapped.\nWhen this land enters, scry 1.\n{T}: Add {W} or {B}.",
        ["B", "W"],
        True, False, ["B", "W"], "always", False,
    ),
    (
        "Godless Shrine",  # shock — escape hatch
        "Land — Plains Swamp",
        "({T}: Add {W} or {B}.)\nAs this land enters, you may pay 2 life. If you don't, it enters tapped.",
        ["B", "W"],
        True, False, ["B", "W"], "conditional", False,
    ),
    (
        "Isolated Chapel",  # check land
        "Land",
        "This land enters tapped unless you control a Plains or a Swamp.\n{T}: Add {W} or {B}.",
        ["B", "W"],
        True, False, ["B", "W"], "conditional", False,
    ),
    (
        "Concealed Courtyard",  # fast land
        "Land",
        "This land enters tapped unless you control two or fewer other lands.\n{T}: Add {W} or {B}.",
        ["B", "W"],
        True, False, ["B", "W"], "conditional", False,
    ),
    (
        "Cinder Glade",  # tango land
        "Land — Mountain Forest",
        "({T}: Add {R} or {G}.)\nThis land enters tapped unless you control two or more basic lands.",
        ["G", "R"],
        True, False, ["G", "R"], "conditional", False,
    ),
    (
        "Dimir Aqueduct",  # bounce land — unconditionally tapped
        "Land",
        "This land enters tapped.\nWhen this land enters, return a land you control to its owner's hand.\n{T}: Add {U}{B}.",
        ["B", "U"],
        True, False, ["B", "U"], "always", False,
    ),
    (
        "Polluted Delta",  # true fetch — names land *types*, not "land"
        "Land",
        "{T}, Pay 1 life, Sacrifice this land: Search your library for an Island or Swamp card, put it onto the battlefield, then shuffle.",
        [],
        True, False, [], "untapped", True,
    ),
    (
        # "onto the battlefield tapped" refers to the searched card — Evolving
        # Wilds itself enters untapped.
        "Evolving Wilds",
        "Land",
        "{T}, Sacrifice this land: Search your library for a basic land card, put it onto the battlefield tapped, then shuffle.",
        [],
        True, False, [], "untapped", True,
    ),
    (
        "Krosan Verge",  # pre-2024 templating + fetch + always tapped
        "Land",
        "Krosan Verge enters the battlefield tapped.\n{2}, {T}, Sacrifice Krosan Verge: Search your library for a Forest card and a Plains card, put them onto the battlefield tapped, then shuffle.",
        [],
        True, False, [], "always", True,
    ),
    (
        "Myriad Landscape",
        "Land",
        "This land enters tapped.\n{T}: Add {C}.\n{2}, {T}, Sacrifice this land: Search your library for up to two basic land cards that share a land type, put them onto the battlefield tapped, then shuffle.",
        ["C"],
        True, False, ["C"], "always", True,
    ),
    (
        # Sacrifices and searches, but the sac ability's own effect is
        # destruction; the symmetric search ramps opponents too.  We
        # deliberately do not classify it as a fetch.
        "Field of Ruin",
        "Land",
        "{T}: Add {C}.\n{2}, {T}, Sacrifice this land: Destroy target nonbasic land an opponent controls. Each player searches their library for a basic land card, puts it onto the battlefield, then shuffles.",
        ["C"],
        True, False, ["C"], "untapped", False,
    ),
    (
        "Ancient Tomb",
        "Land",
        "{T}: Add {C}{C}. This land deals 2 damage to you.",
        ["C"],
        True, False, ["C"], "untapped", False,
    ),
    (
        "Dryad Arbor",
        "Land Creature — Forest Dryad",
        "(Dryad Arbor isn't a spell, it's affected by summoning sickness, and it has \"{T}: Add {G}.\")",
        ["G"],
        True, False, ["G"], "untapped", False,
    ),
    (
        "Llanowar Elves",  # not a land at all
        "Creature — Elf Druid",
        "{T}: Add {G}.",
        ["G"],
        False, False, [], None, False,
    ),
]


@pytest.mark.parametrize(
    ("name", "type_line", "text", "produced", "is_land", "is_basic", "colors", "etb", "fetch"),
    _LANDS,
    ids=[row[0] for row in _LANDS],
)
def test_classify_land(name, type_line, text, produced, is_land, is_basic, colors, etb, fetch):
    facts = classify_land(type_line, text, produced)
    assert facts.is_land is is_land
    assert facts.is_basic is is_basic
    assert sorted(facts.land_colors) == sorted(colors)
    assert facts.etb_tapped == etb
    assert facts.is_fetch is fetch


# ── MDFC detection ────────────────────────────────────────────────────────────

_MALAKIR_FACES = [
    {"faceName": "Malakir Rebirth", "layout": "modal_dfc", "side": "a", "type": "Instant"},
    {"faceName": "Malakir Mire", "layout": "modal_dfc", "side": "b", "type": "Land"},
]
_PATHWAY_FACES = [
    {"faceName": "Needleverge Pathway", "layout": "modal_dfc", "side": "a", "type": "Land"},
    {"faceName": "Pillarverge Pathway", "layout": "modal_dfc", "side": "b", "type": "Land"},
]
_SPLIT_FACES = [
    {"faceName": "Fire", "layout": "split", "side": "a", "type": "Instant"},
    {"faceName": "Ice", "layout": "split", "side": "b", "type": "Instant"},
]
_TRANSFORM_FACES = [
    {"faceName": "Growing Rites of Itlimoc", "layout": "transform", "side": "a", "type": "Legendary Enchantment"},
    {"faceName": "Itlimoc, Cradle of the Sun", "layout": "transform", "side": "b", "type": "Legendary Land"},
]
_MDFC_SPELLS = [  # spell // spell MDFC
    {"faceName": "Alrund's Epiphany", "layout": "modal_dfc", "side": "a", "type": "Sorcery"},
    {"faceName": "Hakka, Whispering Raven", "layout": "modal_dfc", "side": "b", "type": "Legendary Creature — Bird"},
]


@pytest.mark.parametrize(
    ("faces", "expected"),
    [
        (_MALAKIR_FACES, True),      # spell front, land back
        (_PATHWAY_FACES, True),      # land // land
        (_SPLIT_FACES, False),       # split card, no land
        (_TRANSFORM_FACES, False),   # transform: back land is not playable as a land drop
        (_MDFC_SPELLS, False),       # MDFC with no land face
        ([{"layout": "normal", "type": "Instant"}], False),
        (None, False),
        ([], False),
    ],
    ids=["malakir", "pathway", "split", "transform", "mdfc-no-land", "single", "none", "empty"],
)
def test_is_mdfc_land(faces, expected):
    assert is_mdfc_land(faces) is expected


def test_scryfall_face_key_supported():
    faces = [
        {"layout": "modal_dfc", "type_line": "Instant"},
        {"layout": "modal_dfc", "type_line": "Land"},
    ]
    assert is_mdfc_land(faces) is True


# ── End-to-end ────────────────────────────────────────────────────────────────


def test_compute_facts_dismember():
    facts = compute_card_facts("{1}{B/P}{B/P}", "Instant", "Target creature gets -5/-5 until end of turn.", None)
    assert facts.mana.generic == 1
    assert facts.mana.hybrid == [["B", "P"]] * 2
    assert facts.land.is_land is False
    assert facts.land.etb_tapped is None
    assert facts.is_mdfc_land is False


def test_compute_facts_malakir_rebirth():
    # Front face is an instant, but the card still carries its land face's
    # produced colors so the mana-base solver can count it fractionally.
    facts = compute_card_facts(
        "{B}",
        "Instant",
        "Choose target creature. You lose 2 life…",
        ["B"],
        faces=_MALAKIR_FACES,
    )
    assert facts.land.is_land is False
    assert facts.is_mdfc_land is True
    assert facts.land.land_colors == ["B"]
    assert facts.mana.pips == {"B": 1}
