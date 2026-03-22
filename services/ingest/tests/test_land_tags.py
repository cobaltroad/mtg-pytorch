"""Tests for land oracle text augmentation (land_tags.py).

Covers the canonical land cycles most relevant to Commander deckbuilding:
  - Shock lands     (Overgrown Tomb)
  - Check lands     (Woodland Cemetery)
  - Fetch lands     (Verdant Catacombs)
  - Filter lands    (Twilight Mire)
  - Bond lands      (Undergrowth Stadium)
  - Fast lands      (Darkslick Shores)
  - Slow lands      (Shattered Sanctum)
  - Pain lands      (Underground River)
  - Surveil lands   (Meticulous Archive)
  - Gain lands      (Jungle Hollow)
  - Bounce lands    (Dimir Aqueduct)
  - Any-color lands (Command Tower, Mana Confluence)
  - Penalty cases   (Bojuka Bog, Forsaken City, Base Camp)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from land_tags import annotate_land_oracle, build_land_tags  # noqa: E402


# ── Helpers ────────────────────────────────────────────────────────────────────

def tags(oracle: str) -> list[str]:
    return build_land_tags(oracle)


def has(oracle: str, *expected_tags: str) -> bool:
    t = set(tags(oracle))
    return all(e in t for e in expected_tags)


# ── Shock lands ────────────────────────────────────────────────────────────────

OVERGROWN_TOMB = (
    "({T}: Add {B} or {G}.)\n"
    "As Overgrown Tomb enters, you may pay 2 life. If you don't, it enters tapped."
)

def test_shock_land_dual_tag():
    assert "DUAL_LAND:BG" in tags(OVERGROWN_TOMB)

def test_shock_land_cycle_tag():
    assert "SHOCK_LAND" in tags(OVERGROWN_TOMB)

def test_shock_land_not_tapped():
    # Shock lands enter tapped only conditionally; should NOT get ENTERS_TAPPED
    assert "ENTERS_TAPPED" not in tags(OVERGROWN_TOMB)


# ── Check lands ────────────────────────────────────────────────────────────────

WOODLAND_CEMETERY = (
    "Woodland Cemetery enters tapped unless you control a Swamp or a Forest.\n"
    "{T}: Add {B} or {G}."
)

def test_check_land_dual_tag():
    assert "DUAL_LAND:BG" in tags(WOODLAND_CEMETERY)

def test_check_land_cycle_tag():
    assert "CHECK_LAND" in tags(WOODLAND_CEMETERY)

def test_check_land_not_tapped():
    assert "ENTERS_TAPPED" not in tags(WOODLAND_CEMETERY)


# ── Fetch lands ────────────────────────────────────────────────────────────────

VERDANT_CATACOMBS = (
    "{T}, Pay 1 life, Sacrifice Verdant Catacombs: Search your library for a "
    "Swamp or Forest card, put it onto the battlefield, then shuffle."
)

def test_fetch_land_tag():
    assert "FETCH_LAND:BG" in tags(VERDANT_CATACOMBS)

def test_fetch_land_not_dual():
    # Fetch is its own category; should not also get DUAL_LAND
    assert not any(t.startswith("DUAL_LAND") for t in tags(VERDANT_CATACOMBS))


ARID_MESA = (
    "{T}, Pay 1 life, Sacrifice Arid Mesa: Search your library for a Plains or "
    "Mountain card, put it onto the battlefield, then shuffle."
)

def test_fetch_land_wr():
    assert "FETCH_LAND:WR" in tags(ARID_MESA)

MISTY_RAINFOREST = (
    "{T}, Pay 1 life, Sacrifice Misty Rainforest: Search your library for a "
    "Forest or Island card, put it onto the battlefield, then shuffle."
)

def test_fetch_land_ug():
    assert "FETCH_LAND:UG" in tags(MISTY_RAINFOREST)


# ── Filter lands ───────────────────────────────────────────────────────────────

TWILIGHT_MIRE = "{B/G}, {T}: Add {B}{B}, {B}{G}, or {G}{G}."

def test_filter_land_tag():
    assert "FILTER_LAND:BG" in tags(TWILIGHT_MIRE)

def test_filter_land_not_dual():
    assert not any(t.startswith("DUAL_LAND") for t in tags(TWILIGHT_MIRE))


# ── Any-color lands ────────────────────────────────────────────────────────────

COMMAND_TOWER = "{T}: Add one mana of any color in your commander's color identity."

def test_command_tower():
    assert "ANY_COLOR_LAND" in tags(COMMAND_TOWER)
    assert not any(t.startswith("DUAL_LAND") for t in tags(COMMAND_TOWER))

MANA_CONFLUENCE = (
    "{T}, Pay 1 life: Add one mana of any color."
)

def test_mana_confluence():
    assert "ANY_COLOR_LAND" in tags(MANA_CONFLUENCE)


# ── Bond lands ─────────────────────────────────────────────────────────────────

UNDERGROWTH_STADIUM = (
    "({T}: Add {B} or {G}.)\n"
    "Undergrowth Stadium enters tapped unless you have two or more opponents."
)

def test_bond_land_dual_tag():
    assert "DUAL_LAND:BG" in tags(UNDERGROWTH_STADIUM)

def test_bond_land_cycle_tag():
    assert "BOND_LAND" in tags(UNDERGROWTH_STADIUM)

def test_bond_land_not_tapped():
    assert "ENTERS_TAPPED" not in tags(UNDERGROWTH_STADIUM)


# ── Fast lands ─────────────────────────────────────────────────────────────────

DARKSLICK_SHORES = (
    "({T}: Add {U} or {B}.)\n"
    "Darkslick Shores enters tapped unless you control two or fewer other lands."
)

def test_fast_land_dual_tag():
    assert "DUAL_LAND:UB" in tags(DARKSLICK_SHORES)

def test_fast_land_cycle_tag():
    assert "FAST_LAND" in tags(DARKSLICK_SHORES)

def test_fast_land_not_tapped():
    assert "ENTERS_TAPPED" not in tags(DARKSLICK_SHORES)


# ── Slow lands ─────────────────────────────────────────────────────────────────

SHATTERED_SANCTUM = (
    "({T}: Add {W} or {B}.)\n"
    "Shattered Sanctum enters tapped unless you control two or more basic lands."
)

def test_slow_land_dual_tag():
    assert "DUAL_LAND:WB" in tags(SHATTERED_SANCTUM)

def test_slow_land_cycle_tag():
    assert "SLOW_LAND" in tags(SHATTERED_SANCTUM)

def test_slow_land_not_tapped():
    assert "ENTERS_TAPPED" not in tags(SHATTERED_SANCTUM)


# ── Pain lands ─────────────────────────────────────────────────────────────────

UNDERGROUND_RIVER = (
    "{T}: Add {C}.\n"
    "{T}: Add {U} or {B}. Underground River deals 1 damage to you."
)

def test_pain_land_dual_tag():
    # Pain lands produce two colors via {T}: Add {U} or {B}.
    assert "DUAL_LAND:UB" in tags(UNDERGROUND_RIVER)

def test_pain_land_cycle_tag():
    assert "PAIN_LAND" in tags(UNDERGROUND_RIVER)


# ── Surveil lands ──────────────────────────────────────────────────────────────

UNDERCITY_SEWERS = (
    "Undercity Sewers enters tapped.\n"
    "When Undercity Sewers enters, surveil 1.\n"
    "{T}: Add {U} or {B}."
)

def test_surveil_land_dual_tag():
    assert "DUAL_LAND:UB" in tags(UNDERCITY_SEWERS)

def test_surveil_land_cycle_tag():
    assert "SURVEIL_LAND" in tags(UNDERCITY_SEWERS)

def test_surveil_land_tapped():
    assert "ENTERS_TAPPED" in tags(UNDERCITY_SEWERS)


# ── Gain lands ─────────────────────────────────────────────────────────────────

JUNGLE_HOLLOW = (
    "Jungle Hollow enters tapped.\n"
    "When Jungle Hollow enters, you gain 1 life.\n"
    "{T}: Add {B} or {G}."
)

def test_gain_land_dual_tag():
    assert "DUAL_LAND:BG" in tags(JUNGLE_HOLLOW)

def test_gain_land_cycle_tag():
    assert "GAIN_LAND" in tags(JUNGLE_HOLLOW)

def test_gain_land_tapped():
    assert "ENTERS_TAPPED" in tags(JUNGLE_HOLLOW)


# ── Bounce lands ───────────────────────────────────────────────────────────────

DIMIR_AQUEDUCT = (
    "Dimir Aqueduct enters tapped.\n"
    "When Dimir Aqueduct enters, return a land you control to its owner's hand.\n"
    "{T}: Add {U}{B}."
)

def test_bounce_land_dual_tag():
    assert "DUAL_LAND:UB" in tags(DIMIR_AQUEDUCT)

def test_bounce_land_cycle_tag():
    assert "BOUNCE_LAND" in tags(DIMIR_AQUEDUCT)

def test_bounce_land_tapped():
    assert "ENTERS_TAPPED" in tags(DIMIR_AQUEDUCT)


# ── Penalty cases ──────────────────────────────────────────────────────────────

BOJUKA_BOG = (
    "Bojuka Bog enters tapped.\n"
    "When Bojuka Bog enters, exile all cards from target player's graveyard.\n"
    "{T}: Add {B}."
)

def test_bojuka_bog_single_color():
    assert "SINGLE_COLOR_LAND:B" in tags(BOJUKA_BOG)

def test_bojuka_bog_tapped():
    assert "ENTERS_TAPPED" in tags(BOJUKA_BOG)


FORSAKEN_CITY = (
    "At the beginning of your upkeep, sacrifice Forsaken City unless you remove "
    "a card in your hand from the game.\n"
    "This land doesn't untap during your untap step.\n"
    "{T}: Add {U}."
)

def test_forsaken_city_single_color():
    assert "SINGLE_COLOR_LAND:U" in tags(FORSAKEN_CITY)

def test_forsaken_city_doesnt_untap():
    assert "DOESNT_UNTAP" in tags(FORSAKEN_CITY)


BASE_CAMP = (
    "{T}: Add {C}.\n"
    "{T}: Add one mana of any color. Spend this mana only to cast a Warrior spell."
)

def test_base_camp_type_restricted():
    assert "TYPE_RESTRICTED" in tags(BASE_CAMP)


# ── annotate_land_oracle integration ──────────────────────────────────────────

def test_annotate_prepends_tags():
    result = annotate_land_oracle(OVERGROWN_TOMB)
    assert result.startswith("[DUAL_LAND:BG]")
    assert "[SHOCK_LAND]" in result
    assert OVERGROWN_TOMB in result

def test_annotate_empty_oracle():
    assert annotate_land_oracle("") == ""

def test_annotate_basic_land_unchanged():
    # A basic Forest has no qualifying abilities; oracle text unchanged
    basic = "{T}: Add {G}."
    result = annotate_land_oracle(basic)
    # Single color lands get a tag
    assert result.startswith("[SINGLE_COLOR_LAND:G]")

def test_annotate_no_mana_unchanged():
    # A land with no mana abilities (pathological case) returns unchanged
    no_mana = "This land has no special abilities."
    assert annotate_land_oracle(no_mana) == no_mana


# ── Color ordering ─────────────────────────────────────────────────────────────

TEMPLE_GARDEN = (
    "({T}: Add {G} or {W}.)\n"
    "As Temple Garden enters, you may pay 2 life. If you don't, it enters tapped."
)

def test_color_order_wubrg():
    # Colors must always appear in WUBRG order: W before G
    assert "DUAL_LAND:WG" in tags(TEMPLE_GARDEN)
    assert "DUAL_LAND:GW" not in tags(TEMPLE_GARDEN)
