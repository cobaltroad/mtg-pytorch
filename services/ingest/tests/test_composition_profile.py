"""Golden tests for shared/composition (plan W2).

Reference commanders use hand-specified inputs mirroring their real card
data and decompose signals, so the tests stay pure (no DB).  If quota
constants in profile.py are retuned, update the expectations here — the
*relationships* (voltron > engine > vanilla protection, cheap commander →
less ramp, quotas sum to 99) are the real contract.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

for _parent in Path(__file__).resolve().parents:
    if (_parent / "shared" / "composition").is_dir():
        sys.path.insert(0, str(_parent / "shared"))
        break

from composition.karsten import castable_prob, required_sources  # noqa: E402
from composition.profile import DECK_SIZE, derive_profile  # noqa: E402

# ── Karsten math ──────────────────────────────────────────────────────────────


def test_karsten_matches_published_60_card_ballpark():
    # Karsten's 60-card table: T1 single pip ≈ 14, T2 double ≈ 20-21.
    # Our unconditional model runs ~1 source conservative.
    assert required_sources(1, 1, deck_size=60) in (13, 14, 15)
    assert required_sources(2, 2, deck_size=60) in (20, 21, 22)


def test_karsten_monotonic_in_pips_and_turn():
    # More pips → more sources; later turn → fewer sources.
    assert required_sources(3, 2) > required_sources(3, 1)
    assert required_sources(6, 2) < required_sources(2, 2)


def test_castable_prob_bounds():
    assert castable_prob(0, 3, 1) == 0.0
    assert castable_prob(99, 1, 1, deck_size=99) == 1.0
    assert 0.0 < castable_prob(20, 3, 1) < 1.0


def test_required_sources_zero_pips():
    assert required_sources(3, 0) == 0


# ── Reference commander profiles ──────────────────────────────────────────────

WILHELT = dict(  # {2}{U}{B} zombie engine: dies-trigger + tokens + sac payoff
    commander_name="Wilhelt, the Rotcleaver",
    mana_value=4,
    pips={"U": 1, "B": 1},
    color_identity=["U", "B"],
    decompose_keys={"death_trigger", "creature_token_generator", "sacrifice_payoff"},
)

SYR_GWYN = dict(  # {3}{R}{W}{B} voltron: equipment_matters
    commander_name="Syr Gwyn, Hero of Ashvale",
    mana_value=6,
    pips={"R": 1, "W": 1, "B": 1},
    color_identity=["R", "W", "B"],
    decompose_keys={"equipment_matters", "attack_trigger"},
)

ANJE = dict(  # {2}{B}{R} single-signal value commander
    commander_name="Anje Falkenrath",
    mana_value=4,
    pips={"B": 1, "R": 1},
    color_identity=["B", "R"],
    decompose_keys={"discard_outlet"},
)

VANILLA_2DROP = dict(  # signal-less cheap commander
    commander_name="Generic Two-Drop",
    mana_value=2,
    pips={"G": 2},
    color_identity=["G"],
    decompose_keys=set(),
)


@pytest.mark.parametrize(
    "commander", [WILHELT, SYR_GWYN, ANJE, VANILLA_2DROP],
    ids=lambda c: c["commander_name"],
)
def test_quotas_always_sum_to_99(commander):
    profile = derive_profile(**commander)
    assert profile.slot_total() == DECK_SIZE


@pytest.mark.parametrize(
    "commander", [WILHELT, SYR_GWYN, ANJE, VANILLA_2DROP],
    ids=lambda c: c["commander_name"],
)
def test_curve_targets_cover_spell_slots(commander):
    profile = derive_profile(**commander)
    assert sum(t.count for t in profile.curve_targets) == DECK_SIZE - profile.lands.count


@pytest.mark.parametrize(
    "commander", [WILHELT, SYR_GWYN, ANJE, VANILLA_2DROP],
    ids=lambda c: c["commander_name"],
)
def test_every_quota_has_rationale(commander):
    profile = derive_profile(**commander)
    for quota in (profile.lands, profile.ramp, profile.draw, profile.spot_removal,
                  profile.sweepers, profile.protection, profile.theme):
        assert quota.because.strip()
    assert profile.go_live_because.strip()


def test_wilhelt_profile():
    p = derive_profile(**WILHELT)
    assert p.go_live_turn == 3            # 4-drop, ramped out a turn early
    assert p.ramp.count == 10
    assert p.ramp.max_mv == 2             # only ≤2 MV ramp accelerates a 4-drop
    assert p.sweepers.count == 2          # go-wide (token generator) → fewer wipes
    assert p.protection.count == 5        # 3 signals → engine commander
    # Commander castability: U and B requirements at the go-live turn.
    reqs = {r.color: r for r in p.pip_requirements}
    assert set(reqs) == {"U", "B"}
    assert all(r.by_turn == 3 for r in reqs.values())
    assert all(15 <= r.sources <= 25 for r in reqs.values())


def test_voltron_gets_max_protection():
    p = derive_profile(**SYR_GWYN)
    assert p.protection.count == 6
    assert "voltron" in p.protection.because
    assert p.go_live_turn == 5            # 6-drop ramped a turn early
    assert p.ramp.count == 12
    assert p.ramp.max_mv == 4             # mv − 2: Thran Dynamo tier is live


def test_big_mana_commander_ramp_scales():
    p = derive_profile("Kozilek", 10, {}, [], set())
    assert p.ramp.count == 14
    assert p.ramp.max_mv == 5             # ceiling: Gilded Lotus tier


def test_cheap_commander_needs_less_ramp():
    p = derive_profile(**VANILLA_2DROP)
    assert p.go_live_turn == 2
    assert p.ramp.count == 6
    assert p.protection.count == 2        # nothing routes through it
    # Ramp budget freed up flows into theme slots.
    wilhelt_theme = derive_profile(**WILHELT).theme.count
    assert p.theme.count > wilhelt_theme


def test_single_signal_commander_gets_moderate_protection():
    p = derive_profile(**ANJE)
    assert p.protection.count == 3


def test_anthem_commander_is_go_wide():
    # #136 tranche 2: anthem keys imply a wide board — fewer own sweepers.
    p = derive_profile("Kongming", 3, {"W": 1, "U": 1}, ["W", "U"], {"static_pump"})
    assert p.sweepers.count == 2
    assert "goes wide" in p.sweepers.because


def test_activated_engine_gets_engine_protection():
    # Yisan: activated tutor keys force engine-tier protection even though
    # only two signals fire (the deck routes through the activation loop).
    p = derive_profile(
        "Yisan, the Wanderer Bard", 3, {"G": 1}, ["G"],
        {"activated_tutor", "activated_tutor_creature"},
    )
    assert p.protection.count == 5
    assert "activated engine" in p.protection.because


def test_double_pip_demands_more_sources():
    p = derive_profile(**VANILLA_2DROP)  # {G}{G} at turn 2
    (req,) = p.pip_requirements
    assert req.color == "G"
    assert req.pips == 2
    # Double pip on turn 2 in a 99-card deck is brutal — the number should
    # say so (mono-color decks satisfy it trivially; multicolor can't).
    assert req.sources >= 30


def test_partner_profile_merges_and_shrinks_deck():
    """#147: union identity + signals, 98 cards, per-partner pip clocks."""
    from composition.profile import derive_partner_profile

    p = derive_partner_profile([
        dict(name="Rograkh, Son of Rohgahh", mana_value=0, pips={},
             color_identity=["R"], decompose_keys=set()),
        dict(name="Silas Renn, Seeker Adept", mana_value=3, pips={"U": 1, "B": 1},
             color_identity=["U", "B"], decompose_keys={"artifact_count"}),
    ])
    assert p.deck_size == 98
    assert p.slot_total() == 98
    assert p.color_identity == ["B", "R", "U"]
    assert "artifact_count" in p.signals
    assert p.commander_mv == 3                     # lead = higher MV
    assert p.partner_cast == {"mv": 0, "pips": {}}  # Rograkh rides along
    # Silas must be castable on HIS clock (turn 3), not the lead's.
    assert all(r.by_turn == 3 for r in p.pip_requirements)


def test_partner_profile_requires_two():
    from composition.profile import derive_partner_profile

    with pytest.raises(ValueError):
        derive_partner_profile([
            dict(name="Solo", mana_value=3, pips={}, color_identity=["G"],
                 decompose_keys=set()),
        ])


def test_as_dict_is_json_shaped():
    d = derive_profile(**WILHELT).as_dict()
    assert d["quotas"]["ramp"]["max_mv"] == 2
    assert d["go_live_turn"]["turn"] == 3
    assert isinstance(d["curve_targets"], list)
    assert isinstance(d["pip_requirements"][0]["sources"], int)
