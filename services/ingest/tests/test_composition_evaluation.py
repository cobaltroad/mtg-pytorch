"""Tests for shared/composition/evaluation.py (plan W6).

Reuses the synthetic pools from test_composition_builder to produce real
BuildResults, then verifies the checks catch what they claim to catch.
"""

from __future__ import annotations

import sys
from pathlib import Path

for _parent in Path(__file__).resolve().parents:
    if (_parent / "shared" / "composition").is_dir():
        sys.path.insert(0, str(_parent / "shared"))
        break

from composition.builder import build_deck  # noqa: E402
from composition.evaluation import check_build, deck_census, range_check  # noqa: E402

from tests.test_composition_builder import WILHELT_PROFILE, make_pools  # noqa: E402


def _build():
    pools, land_pool, basics, forced = make_pools()
    return build_deck(WILHELT_PROFILE, pools, land_pool, basics, forced=forced,
                      goldfish_games=300)


def _ci_map(result, identity=("U", "B")):
    return {c["id"]: set(identity) for c in result.deck}


def test_sound_build_passes_all_checks():
    r = _build()
    assert check_build(WILHELT_PROFILE, r, {"U", "B"}, _ci_map(r)) == []


def test_census_matches_profile():
    r = _build()
    census = deck_census(r)
    assert census["ramp"] == WILHELT_PROFILE.ramp.count
    assert census["draw"] == WILHELT_PROFILE.draw.count
    assert census["protection"] == WILHELT_PROFILE.protection.count
    assert sum(census.values()) == 99


def test_color_violation_detected():
    r = _build()
    ci = _ci_map(r)
    offender = next(c for c in r.deck if not c["is_basic"])
    ci[offender["id"]] = {"R"}  # claim a card is red in a UB deck
    failures = check_build(WILHELT_PROFILE, r, {"U", "B"}, ci)
    assert any("color identity violation" in f for f in failures)


def test_short_deck_detected():
    r = _build()
    r.deck.pop()
    failures = check_build(WILHELT_PROFILE, r, {"U", "B"}, _ci_map(r))
    assert any("expected 99" in f for f in failures)


def test_duplicate_detected():
    r = _build()
    dupe = next(c for c in r.deck if not c["is_basic"])
    r.deck[-1] = dupe
    failures = check_build(WILHELT_PROFILE, r, {"U", "B"}, _ci_map(r))
    assert any("singleton violation" in f for f in failures)


def test_silent_quota_shortfall_detected():
    r = _build()
    # Remove a protection card from the breakdown without any warning —
    # a silent deviation must fail the audit.
    r.breakdown["protection"].pop()
    failures = check_build(WILHELT_PROFILE, r, {"U", "B"}, _ci_map(r))
    assert any("quota mismatch: protection" in f for f in failures)


def test_warned_shortfall_tolerated():
    pools, land_pool, basics, forced = make_pools()
    pools["protection"] = pools["protection"][:2]  # starve the pool
    r = build_deck(WILHELT_PROFILE, pools, land_pool, basics, forced=forced,
                   goldfish_games=300)
    assert any("protection pool exhausted" in w for w in r.warnings)
    failures = check_build(WILHELT_PROFILE, r, {"U", "B"}, _ci_map(r))
    assert not any("protection" in f for f in failures)


def test_range_check():
    stats = {"lands": {"min": 33, "max": 40}, "ramp": {"min": 5, "max": 14}}
    assert range_check({"lands": 36, "ramp": 10}, stats) == []
    notes = range_check({"lands": 50, "ramp": 10}, stats)
    assert len(notes) == 1 and "lands" in notes[0]
