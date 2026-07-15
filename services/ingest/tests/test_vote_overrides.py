"""Tests for the amend pass (#184) — vote overrides on composition builds.

Pure layer: apply_vote_overrides (pool_helpers).  Builder layer: 'pinned'
role exemptions in the three cut paths (feedback-loop land conversion,
wincon-audit swap, pip-offender swap) — pins survive cuts but never
bypass quotas or the castability gate.
"""

from __future__ import annotations

import sys
from itertools import count
from pathlib import Path

for _parent in Path(__file__).resolve().parents:
    if (_parent / "shared" / "composition").is_dir():
        sys.path.insert(0, str(_parent / "shared"))
        break

from composition.builder import build_deck  # noqa: E402
from composition.pool_helpers import apply_vote_overrides  # noqa: E402
from composition.profile import derive_profile  # noqa: E402

_ids = count()


def card(name, mv=2, pips=None, is_land=False, is_basic=False, produces=None,
         etb_tapped=None, is_fetch=False, roles=None, power=0):
    return {
        "id": f"v{next(_ids)}", "name": name, "mv": mv, "pips": pips or {},
        "hybrid": [], "is_land": is_land, "is_basic": is_basic,
        "produces": produces or [], "etb_tapped": etb_tapped,
        "is_fetch": is_fetch, "roles": roles or set(), "power": power,
    }


def make_pools():
    pools = {
        "ramp": [card(f"Rock {i}", mv=2, produces=["C"], roles={"ramp"}) for i in range(15)],
        "draw_engine": [card(f"Engine {i}", mv=3, pips={"U": 1}) for i in range(10)],
        "draw_spell": [card(f"Divination {i}", mv=2, pips={"U": 1}) for i in range(10)],
        "spot_removal": [card(f"Removal {i}", mv=2, pips={"B": 1}) for i in range(12)],
        "sweeper": [card(f"Wipe {i}", mv=4, pips={"B": 2}) for i in range(6)],
        "protection": [card(f"Boots {i}", mv=2) for i in range(8)],
        "theme": [card(f"Zombie {i}", mv=(i % 5) + 1, pips={"B": 1}) for i in range(60)],
        "wincon": [card(f"Finisher {i}", mv=5, pips={"B": 1}) for i in range(4)],
    }
    land_pool = [
        card(f"Dual {i}", is_land=True, produces=["U", "B"], etb_tapped="conditional")
        for i in range(12)
    ] + [
        card(f"Tapland {i}", is_land=True, produces=["U", "B"], etb_tapped="always")
        for i in range(8)
    ]
    basics = {
        "U": card("Island", is_land=True, is_basic=True, produces=["U"], etb_tapped="untapped"),
        "B": card("Swamp", is_land=True, is_basic=True, produces=["B"], etb_tapped="untapped"),
    }
    forced = [
        card("Sol Ring", mv=1, produces=["C"], roles={"ramp"}),
        card("Arcane Signet", mv=2, produces=["U", "B"], roles={"ramp"}),
        card("Command Tower", is_land=True, produces=["U", "B"], etb_tapped="untapped"),
    ]
    return pools, land_pool, basics, forced


PROFILE = derive_profile(
    "Wilhelt", 4, {"U": 1, "B": 1}, ["U", "B"],
    {"death_trigger", "creature_token_generator", "sacrifice_payoff"},
)


# ── apply_vote_overrides (pure) ──────────────────────────────────────────────

def test_downvoted_excluded_everywhere():
    pools, land_pool, basics, forced = make_pools()
    down = {pools["theme"][0]["id"], pools["ramp"][3]["id"],
            land_pool[0]["id"], forced[0]["id"]}
    new_pools, new_land, new_forced, pinned, unplaced = apply_vote_overrides(
        pools, land_pool, forced, set(), down
    )
    remaining = {c["id"] for p in new_pools.values() for c in p}
    remaining |= {c["id"] for c in new_land} | {c["id"] for c in new_forced}
    assert not down & remaining
    assert pinned == set() and unplaced == set()


def test_upvoted_fronted_and_tagged():
    pools, land_pool, basics, forced = make_pools()
    fringe = pools["theme"][45]
    new_pools, *_ , pinned, unplaced = apply_vote_overrides(
        pools, land_pool, forced, {fringe["id"]}, set()
    )
    assert new_pools["theme"][0]["id"] == fringe["id"]
    assert "pinned" in fringe["roles"]
    assert pinned == {fringe["id"]} and unplaced == set()


def test_unplaced_pin_reported():
    pools, land_pool, basics, forced = make_pools()
    *_, pinned, unplaced = apply_vote_overrides(
        pools, land_pool, forced, {"ghost-id"}, set()
    )
    assert unplaced == {"ghost-id"} and pinned == set()


# ── builder integration ──────────────────────────────────────────────────────

def test_downvoted_card_absent_from_deck():
    # Baseline: Zombie 2 (top of the theme pool) makes the deck.
    pools, land_pool, basics, forced = make_pools()
    baseline = build_deck(PROFILE, pools, land_pool, basics, forced=forced,
                          goldfish_games=300)
    assert any(c["name"] == "Zombie 2" for c in baseline.deck)

    # Downvoted: it's gone, deck still complete.
    pools2, land2, basics2, forced2 = make_pools()
    kill = next(c["id"] for c in pools2["theme"] if c["name"] == "Zombie 2")
    pools2, land2, forced2, _, _ = apply_vote_overrides(
        pools2, land2, forced2, set(), {kill}
    )
    r2 = build_deck(PROFILE, pools2, land2, basics2, forced=forced2,
                    goldfish_games=300)
    assert not any(c["name"] == "Zombie 2" for c in r2.deck)
    assert len(r2.deck) == 99


def test_pinned_fringe_card_included_and_survives():
    pools, land_pool, basics, forced = make_pools()
    fringe = next(c for c in pools["theme"] if c["name"] == "Zombie 55")
    pools, land_pool, forced, _, _ = apply_vote_overrides(
        pools, land_pool, forced, {fringe["id"]}, set()
    )
    r = build_deck(PROFILE, pools, land_pool, basics, forced=forced,
                   goldfish_games=300)
    assert any(c["name"] == "Zombie 55" for c in r.deck)
    assert len(r.deck) == 99


def test_pins_do_not_bypass_quotas():
    # Pinning many theme cards must not inflate the theme quota.
    pools, land_pool, basics, forced = make_pools()
    pins = {c["id"] for c in pools["theme"][:20]}
    pools, land_pool, forced, _, _ = apply_vote_overrides(
        pools, land_pool, forced, pins, set()
    )
    r = build_deck(PROFILE, pools, land_pool, basics, forced=forced,
                   goldfish_games=300)
    assert len(r.deck) == 99
    theme_count = len(r.breakdown["theme"])
    assert theme_count <= PROFILE.theme.count


def test_all_pinned_theme_still_terminates():
    # Even with every theme card pinned, the feedback loop must terminate
    # (it may fail the gate with a warning, never hang or crash).
    pools, land_pool, basics, forced = make_pools()
    pins = {c["id"] for c in pools["theme"]}
    pools, land_pool, forced, _, _ = apply_vote_overrides(
        pools, land_pool, forced, pins, set()
    )
    r = build_deck(PROFILE, pools, land_pool, basics, forced=forced,
                   goldfish_games=300)
    assert len(r.deck) == 99
