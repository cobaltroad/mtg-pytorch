"""Tests for the composition builder + goldfisher (plan W3).

Synthetic card pools — pure, no DB.  The contract under test:
  * exactly 99 cards, singleton except basics
  * quotas honored when pools suffice; shortfalls become basics + warnings
  * ramp respects the profile's max-MV ceiling
  * basics allocation meets the commander's Karsten color minimums first
  * the goldfish feedback loop converts theme slots to lands when the
    castability gate fails, and a sound build passes the gate
"""

from __future__ import annotations

import sys
from itertools import count
from pathlib import Path

import pytest

for _parent in Path(__file__).resolve().parents:
    if (_parent / "shared" / "composition").is_dir():
        sys.path.insert(0, str(_parent / "shared"))
        break

from composition.builder import build_deck, castability_floor, land_quality  # noqa: E402
from composition.goldfish import simulate  # noqa: E402
from composition.profile import derive_profile  # noqa: E402

_ids = count()


def card(name, mv=2, pips=None, is_land=False, is_basic=False, produces=None,
         etb_tapped=None, is_fetch=False, roles=None, power=0):
    return {
        "id": f"c{next(_ids)}", "name": name, "mv": mv, "pips": pips or {},
        "hybrid": [], "is_land": is_land, "is_basic": is_basic,
        "produces": produces or [], "etb_tapped": etb_tapped,
        "is_fetch": is_fetch, "roles": roles or set(), "power": power,
    }


def make_pools():
    """Ample ranked pools for a UB commander."""
    pools = {
        "ramp": [card(f"Rock {i}", mv=2, produces=["C"], roles={"ramp"}) for i in range(15)]
        + [card("Big Rock", mv=4, produces=["C"], roles={"ramp"})],
        "draw_engine": [card(f"Engine {i}", mv=3, pips={"U": 1}) for i in range(10)],
        "draw_spell": [card(f"Divination {i}", mv=2, pips={"U": 1}) for i in range(10)],
        "spot_removal": [card(f"Removal {i}", mv=2, pips={"B": 1}) for i in range(12)],
        "sweeper": [card(f"Wipe {i}", mv=4, pips={"B": 2}) for i in range(6)],
        "protection": [card(f"Boots {i}", mv=2) for i in range(8)],
        "theme": [card(f"Zombie {i}", mv=(i % 5) + 1, pips={"B": 1}) for i in range(60)],
    }
    land_pool = (
        [card(f"Dual {i}", is_land=True, produces=["U", "B"], etb_tapped="conditional")
         for i in range(12)]
        + [card(f"Tapland {i}", is_land=True, produces=["U", "B"], etb_tapped="always")
           for i in range(8)]
    )
    basics = {
        "U": card("Island", is_land=True, is_basic=True, produces=["U"], etb_tapped="untapped"),
        "B": card("Swamp", is_land=True, is_basic=True, produces=["B"], etb_tapped="untapped"),
    }
    pools["wincon"] = [card(f"Finisher {i}", mv=5, pips={"B": 1}) for i in range(4)]
    forced = [
        card("Sol Ring", mv=1, produces=["C"], roles={"ramp"}),
        card("Arcane Signet", mv=2, produces=["U", "B"], roles={"ramp"}),
        card("Command Tower", is_land=True, produces=["U", "B"], etb_tapped="untapped"),
    ]
    return pools, land_pool, basics, forced


WILHELT_PROFILE = derive_profile(
    "Wilhelt", 4, {"U": 1, "B": 1}, ["U", "B"],
    {"death_trigger", "creature_token_generator", "sacrifice_payoff"},
)


def _build(**kw):
    pools, land_pool, basics, forced = make_pools()
    return build_deck(WILHELT_PROFILE, pools, land_pool, basics, forced=forced,
                      goldfish_games=300, **kw)


def test_exactly_99_singleton_except_basics():
    r = _build()
    assert len(r.deck) == 99
    nonbasic_ids = [c["id"] for c in r.deck if not c["is_basic"]]
    assert len(nonbasic_ids) == len(set(nonbasic_ids))


def test_quotas_honored():
    r = _build()
    p = WILHELT_PROFILE
    assert len(r.breakdown["ramp"]) + 2 == p.ramp.count          # +2 forced rocks
    assert len(r.breakdown["draw_engine"]) == p.draw.engines
    assert len(r.breakdown["draw_spell"]) == p.draw.spells
    assert len(r.breakdown["spot_removal"]) == p.spot_removal.count
    assert len(r.breakdown["sweeper"]) == p.sweepers.count
    assert len(r.breakdown["protection"]) == p.protection.count
    lands = sum(1 for c in r.deck if c["is_land"])
    assert lands >= p.lands.count  # feedback loop may add, never remove


def test_ramp_respects_max_mv():
    r = _build()
    assert "Big Rock" not in r.breakdown["ramp"]  # mv 4 > ceiling 2


def test_sound_build_passes_gate():
    r = _build()
    assert r.gate == castability_floor(4, 2) == 0.75  # MV 4, {U}{B} = 2 pips
    assert r.gate_passed, (r.goldfish, r.warnings)


def test_castability_floor_scales_with_pips():
    # Atraxa (MV 4, 4 pips) gets a lower bar than Wilhelt (MV 4, 2 pips):
    # no mana base fully recovers a 4-color pip requirement.
    assert castability_floor(4, 4) < castability_floor(4, 2) < castability_floor(4, 1)
    assert castability_floor(10, 10) >= 0.30  # clamped
    assert castability_floor(10, 1) < castability_floor(6, 1)  # MV-graded past 6
    assert castability_floor(6, 6) < castability_floor(6, 4)  # pip-dense tail headroom


def test_deterministic_under_seed():
    a, b = _build(seed=42), _build(seed=42)
    assert [c["name"] for c in a.deck] == [c["name"] for c in b.deck]


def test_basics_meet_commander_minimums():
    r = _build()
    reqs = {q.color: q.sources for q in WILHELT_PROFILE.pip_requirements}
    for color, needed in reqs.items():
        have = sum(1 for c in r.deck if c["is_land"] and color in c["produces"])
        assert have >= min(needed, 20), (color, have, needed)


def test_pool_shortfall_becomes_basics_with_warning():
    pools, land_pool, basics, forced = make_pools()
    pools["theme"] = pools["theme"][:5]  # starve the theme pool
    r = build_deck(WILHELT_PROFILE, pools, land_pool, basics, forced=forced,
                   goldfish_games=300)
    assert len(r.deck) == 99
    assert any("theme pool exhausted" in w for w in r.warnings)


def test_feedback_loop_fires_on_bad_mana_base():
    """Starve the land pool of untapped lands: gate should force extra lands
    or a documented failure — never a silent pass."""
    pools, land_pool, basics, forced = make_pools()
    land_pool = [c for c in land_pool if c["etb_tapped"] == "always"]
    r = build_deck(WILHELT_PROFILE, pools, land_pool, basics, forced=forced,
                   goldfish_games=300)
    assert r.gate_passed or any("castability gate" in w for w in r.warnings)


def test_land_quality_ordering():
    identity = {"U", "B"}
    dual_untapped = card("x", is_land=True, produces=["U", "B"], etb_tapped="untapped")
    dual_tapped = card("x", is_land=True, produces=["U", "B"], etb_tapped="always")
    mono = card("x", is_land=True, produces=["U"], etb_tapped="untapped")
    off_color = card("x", is_land=True, produces=["R", "G"], etb_tapped="untapped")
    q = [land_quality(c, identity) for c in (dual_untapped, dual_tapped, mono, off_color)]
    assert q == sorted(q, reverse=True)


def test_theme_diminishing_returns():
    """A pool dominated by one sub-theme gets capped so minority sub-themes
    still land their slots."""
    pools, land_pool, basics, forced = make_pools()
    sac = [dict(card(f"Sac Outlet {i}", mv=2, pips={"B": 1}), theme_keys={"sacrifice_payoff"})
           for i in range(50)]
    tokens = [dict(card(f"Token Maker {i}", mv=3, pips={"B": 1}), theme_keys={"token_generator"})
              for i in range(20)]
    pools["theme"] = sac + tokens  # ranked order: all sac outlets first
    r = build_deck(WILHELT_PROFILE, pools, land_pool, basics, forced=forced,
                   goldfish_games=300)
    picked_tokens = sum(1 for n in r.breakdown["theme"] if n.startswith("Token Maker"))
    picked_sac = sum(1 for n in r.breakdown["theme"] if n.startswith("Sac Outlet"))
    quota = WILHELT_PROFILE.theme.count
    cap = max(3, -(-quota // 2) + 2)
    assert picked_sac <= cap                  # majority sub-theme saturates
    assert picked_tokens >= quota - cap - 1   # minority sub-theme gets the rest


def test_goldfish_fetches_resolve_to_needed_colors():
    """#144: a fetch with empty produces finds a needed-color basic
    same-turn; a plain colorless land never pays colored pips."""
    fetch = card("Fetch", is_land=True, is_fetch=True, etb_tapped="untapped")
    wastes = card("Wastes", is_land=True, produces=["C"], etb_tapped="untapped")
    spell = card("Bear", mv=2, pips={"G": 1})
    fetch_deck = [dict(fetch, id=f"f{i}") for i in range(37)] + [dict(spell, id=f"s{i}") for i in range(62)]
    dead_deck = [dict(wastes, id=f"w{i}") for i in range(37)] + [dict(spell, id=f"s{i}") for i in range(62)]
    p_fetch = simulate(fetch_deck, 2, {"G": 2}, 2, games=400).p_commander_by_go_live
    p_dead = simulate(dead_deck, 2, {"G": 2}, 2, games=400).p_commander_by_go_live
    assert p_fetch > 0.85
    assert p_dead == 0.0


def test_goldfish_mdfc_spell_faces_are_land_drops():
    """#143: a hand of spell-front MDFC land faces still makes land drops."""
    mdfc = card("Malakir Rebirth", mv=1, pips={"B": 1}, produces=["B"])
    mdfc["is_mdfc_land"] = True
    spell = card("Filler", mv=3, pips={"B": 1})
    deck = [dict(mdfc, id=f"m{i}") for i in range(37)] + [dict(spell, id=f"s{i}") for i in range(62)]
    r = simulate(deck, 2, {"B": 1}, 4, games=300)
    # MDFC drops come online a turn late but the commander still lands.
    assert r.p_commander_by_go_live > 0.8


def test_fetches_count_toward_pip_minimums():
    """#144: fetch-heavy mana bases satisfy per-color source minimums."""
    pools, _, basics, forced = make_pools()
    land_pool = [card(f"Fetch {i}", is_land=True, is_fetch=True, etb_tapped="untapped")
                 for i in range(18)]
    r = build_deck(WILHELT_PROFILE, pools, land_pool, basics, forced=forced,
                   goldfish_games=300)
    assert not any("minimums unreachable" in w for w in r.warnings)
    assert r.gate_passed, r.warnings


def test_mdfc_spells_grant_land_credit():
    """#143: two spell-slot MDFC land faces free one real land slot."""
    pools, land_pool, basics, forced = make_pools()
    # Spread across curve buckets so bucket capacity can't exclude them all.
    mdfc_theme = [dict(card(f"MDFC {i}", mv=(i % 5) + 1, pips={"B": 1}, produces=["B"]),
                       is_mdfc_land=True, theme_keys={"death_trigger"})
                  for i in range(10)]
    pools["theme"] = mdfc_theme + pools["theme"]
    r = build_deck(WILHELT_PROFILE, pools, land_pool, basics, forced=forced,
                   goldfish_games=300)
    assert any("MDFC land faces" in w for w in r.warnings), r.warnings
    assert len(r.deck) == 99
    # evaluation accepts the credited floor
    from composition.evaluation import check_build
    ci = {c["id"]: {"U", "B"} for c in r.deck}
    assert check_build(WILHELT_PROFILE, r, {"U", "B"}, ci) == []


def test_wincon_audit_forces_finishers():
    """#141: a wincon-less theme gets WINCON_MIN finishers swapped in."""
    from composition.builder import WINCON_MIN

    r = _build()  # make_pools theme has no wincons
    assert len(r.breakdown["wincon"]) == WINCON_MIN
    wincons = sum(1 for c in r.deck if "wincon" in (c.get("roles") or set()))
    assert wincons >= WINCON_MIN
    assert len(r.deck) == 99


def test_wincon_audit_skips_when_theme_already_wins():
    """A theme that already carries finishers is not touched."""
    pools, land_pool, basics, forced = make_pools()
    themed_wincons = [dict(card(f"Theme Finisher {i}", mv=4, pips={"B": 1}),
                           theme_keys={"death_trigger"})
                      for i in range(3)]
    pools["wincon"] = themed_wincons + pools["wincon"]
    pools["theme"] = themed_wincons + pools["theme"]
    r = build_deck(WILHELT_PROFILE, pools, land_pool, basics, forced=forced,
                   goldfish_games=300)
    assert r.breakdown["wincon"] == []  # nothing forced
    wincons = sum(1 for c in r.deck if "wincon" in (c.get("roles") or set()))
    assert wincons >= 2


def test_wincon_pool_exhausted_warns_and_is_tolerated():
    from composition.evaluation import check_build

    pools, land_pool, basics, forced = make_pools()
    pools["wincon"] = []
    r = build_deck(WILHELT_PROFILE, pools, land_pool, basics, forced=forced,
                   goldfish_games=300)
    assert any("no win path" in w for w in r.warnings)
    ci = {c["id"]: {"U", "B"} for c in r.deck}
    assert check_build(WILHELT_PROFILE, r, {"U", "B"}, ci) == []


def test_silent_wincon_shortfall_fails_evaluation():
    from composition.evaluation import check_build

    r = _build()
    for c in r.deck:  # fabricate: strip wincon tags without a warning
        c.get("roles", set()).discard("wincon")
    failures = check_build(WILHELT_PROFILE, r, {"U", "B"},
                           {c["id"]: {"U", "B"} for c in r.deck})
    assert any("win-path audit failed" in f for f in failures)


def test_strategy_win_path_skips_forcing():
    """Voltron/counters/anthem commanders ARE the win path — no forced
    finishers even with a wincon-less theme."""
    p = derive_profile("UB Voltron", 4, {"U": 1, "B": 1}, ["U", "B"],
                       {"equipment_matters", "attack_trigger"})
    pools, land_pool, basics, forced = make_pools()
    r = build_deck(p, pools, land_pool, basics, forced=forced, goldfish_games=300)
    assert r.breakdown["wincon"] == []
    assert "strategy win path" in r.win_path


def test_combat_mass_win_path_skips_forcing():
    """A stompy theme of power-5+ bodies needs no dedicated Overrun."""
    pools, land_pool, basics, forced = make_pools()
    pools["theme"] = [dict(card(f"Fatty {i}", mv=(i % 4) + 3, pips={"B": 1}, power=6),
                           theme_keys={"death_trigger"}) for i in range(30)]
    r = build_deck(WILHELT_PROFILE, pools, land_pool, basics, forced=forced,
                   goldfish_games=300)
    assert r.breakdown["wincon"] == []
    assert "combat mass" in r.win_path


def test_drain_density_win_path_skips_forcing():
    """An aristocrats theme dense in Blood Artist-class drains wins by
    attrition — no forced finishers."""
    pools, land_pool, basics, forced = make_pools()
    drains = [dict(card(f"Drainer {i}", mv=(i % 3) + 1, pips={"B": 1}),
                   theme_keys={"death_trigger"}) for i in range(12)]
    pools["theme"] = drains + pools["theme"]
    pools["drain"] = drains
    r = build_deck(WILHELT_PROFILE, pools, land_pool, basics, forced=forced,
                   goldfish_games=300)
    assert r.breakdown["wincon"] == []
    assert "drain density" in r.win_path


def test_sort_pool_popularity_prior():
    """#140: EDHREC rank orders pools; ramp keeps mana output primary;
    unranked cards tail out."""
    from composition.pool_helpers import UNRANKED, sort_pool

    def pc(name, rank, mv=2, output=1):
        c = card(name, mv=mv)
        c["edhrec_rank"] = rank if rank is not None else UNRANKED
        c["mana_output"] = output
        return c

    pool = [pc("Junk Draw", None), pc("Rhystic Study", 44, mv=3), pc("Divination", 800)]
    assert [c["name"] for c in sort_pool(pool, "draw_engine")] == [
        "Rhystic Study", "Divination", "Junk Draw",
    ]
    ramp = [pc("Popular Trinket", 50, output=1), pc("Thran Dynamo", 400, mv=4, output=3),
            pc("Obscure Rock", None, output=1)]
    assert [c["name"] for c in sort_pool(ramp, "ramp")] == [
        "Thran Dynamo", "Popular Trinket", "Obscure Rock",
    ]


def test_land_quality_fetch_and_mdfc():
    identity = {"U", "B"}
    fetch = card("x", is_land=True, is_fetch=True, etb_tapped="untapped")
    dual_tapped = card("x", is_land=True, produces=["U", "B"], etb_tapped="always")
    mdfc = dict(card("x", produces=["B"]), is_mdfc_land=True)
    off_color = card("x", is_land=True, produces=["R"], etb_tapped="untapped")
    # fetch (finds either color) beats a tapped dual; MDFC beats off-color
    assert land_quality(fetch, identity) > land_quality(dual_tapped, identity)
    assert land_quality(mdfc, identity) > land_quality(off_color, identity)


def test_goldfish_generic_discount():
    """#142: a Karador-shaped cost ({5}{B}{G}{W}) with generic_discount
    casts turns earlier than without — colored pips never shrink."""
    land = card("Tri", is_land=True, produces=["B", "G", "W"], etb_tapped="untapped")
    spell = card("Filler", mv=3)
    deck = ([dict(land, id=f"l{i}") for i in range(37)]
            + [dict(spell, id=f"s{i}") for i in range(62)])
    pips = {"B": 1, "G": 1, "W": 1}
    p_plain = simulate(deck, 8, pips, 5, games=400).p_commander_by_go_live
    p_disc = simulate(deck, 8, pips, 5, games=400, generic_discount=True).p_commander_by_go_live
    assert p_disc > p_plain + 0.3   # discount transforms castability
    # Floor: even with full generic discount the 3 colored pips remain —
    # never castable before 3 sources exist.
    p_early = simulate(deck, 8, pips, 2, games=400, generic_discount=True).p_commander_by_go_live
    assert p_early < 0.9


def test_builder_cost_reduction_passes_unrelaxed_gate():
    """#142 acceptance: the gate is NOT relaxed; the simulation carries it."""
    p = derive_profile("Karador-ish", 8, {"B": 1, "G": 1, "W": 1}, ["B", "G", "W"],
                       {"graveyard_payoff"})
    pools, land_pool, basics, forced = make_pools()
    tri = [card(f"Tri {i}", is_land=True, produces=["B", "G", "W"], etb_tapped="untapped")
           for i in range(20)]
    basics = {c: card(n, is_land=True, is_basic=True, produces=[c], etb_tapped="untapped")
              for c, n in (("B", "Swamp"), ("G", "Forest"), ("W", "Plains"))}
    r = build_deck(p, pools, tri, basics, forced=[], goldfish_games=300,
                   cost_reduction=True)
    assert r.gate == castability_floor(8, 3)  # unrelaxed
    assert not any("relaxed" in w for w in r.warnings)
    assert r.gate_passed, (r.goldfish, r.warnings)


def test_pip_offender_swap_semantics():
    """#146: the scarce-color pip hog is swapped for a light alternative;
    wincons are never the offender."""
    from composition.builder import _pip_offender_swap

    identity = {"W", "U", "B", "G"}
    lands = ([card(f"W{i}", is_land=True, produces=["W"]) for i in range(8)]
             + [card(f"U{i}", is_land=True, produces=["U"]) for i in range(8)]
             + [card(f"G{i}", is_land=True, produces=["G"]) for i in range(8)]
             + [card("OneSwamp", is_land=True, produces=["B"])])  # B is scarce
    hog = card("Necropotence", mv=3, pips={"B": 3})
    winc = card("Torment", mv=6, pips={"B": 2}, roles={"wincon"})
    light = card("Sol Trinket", mv=2, pips={})
    spells = [hog, winc]
    theme = [hog, winc]
    deck = lands + spells
    chosen = {hog["id"], winc["id"]}
    breakdown = {"theme": [hog["name"], winc["name"]], "filler": []}
    pools = {"theme": [dict(light)]}
    warnings = []
    swapped = _pip_offender_swap(deck, spells, theme, chosen, breakdown, pools,
                                 set(), set(), identity, warnings)
    assert swapped and "Necropotence" in swapped
    assert hog not in spells and winc in spells       # wincon untouched
    assert any(c["name"] == "Sol Trinket" for c in spells)
    assert any("pip relief" in w for w in warnings)


def test_pip_offender_swap_declines_light_themes():
    """No swap when nothing is pip-dense enough to be worth a slot."""
    from composition.builder import _pip_offender_swap

    identity = {"U", "B"}
    lands = [card(f"L{i}", is_land=True, produces=["U", "B"]) for i in range(10)]
    mild = card("Mild", mv=2, pips={"B": 1})
    spells, theme = [mild], [mild]
    assert _pip_offender_swap(lands + spells, spells, theme, {mild["id"]},
                              {"theme": [mild["name"]]}, {"theme": [card("Alt")]},
                              set(), set(), identity, []) is None


def test_goldfish_ramp_respects_colored_costs():
    """#145: a {G} dork is not castable off Swamps — off-color ramp must
    not accelerate the commander; on-color ramp must."""
    swamp = card("Swamp", is_land=True, produces=["B"], etb_tapped="untapped")
    forest = card("Forest", is_land=True, produces=["G"], etb_tapped="untapped")
    dork = card("Elf", mv=1, pips={"G": 1}, produces=["G"], roles={"ramp"})
    spell = card("Filler", mv=3, pips={})

    def deck(land):
        return ([dict(land, id=f"l{i}") for i in range(36)]
                + [dict(dork, id=f"d{i}") for i in range(10)]
                + [dict(spell, id=f"s{i}") for i in range(53)])

    # 4-drop by T3 requires the dorks to actually cast.
    p_off = simulate(deck(swamp), 4, {"B": 1}, 3, games=400).p_commander_by_go_live
    p_on = simulate(deck(forest), 4, {"G": 1}, 3, games=400).p_commander_by_go_live
    assert p_on > 0.3
    assert p_off < 0.05  # dorks are dead cards off Swamps


def test_goldfish_prefers_more_lands():
    land = card("Forest", is_land=True, produces=["G"], etb_tapped="untapped")
    spell = card("Bear", mv=2, pips={"G": 1})
    good = [dict(land, id=f"l{i}") for i in range(37)] + [dict(spell, id=f"s{i}") for i in range(62)]
    bad = [dict(land, id=f"l{i}") for i in range(20)] + [dict(spell, id=f"s{i}") for i in range(79)]
    pg = simulate(good, 2, {"G": 2}, 2, games=400).p_commander_by_go_live
    pb = simulate(bad, 2, {"G": 2}, 2, games=400).p_commander_by_go_live
    assert pg > pb + 0.2
