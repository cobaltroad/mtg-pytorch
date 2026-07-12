"""Evaluation checks for built decks — the harness's pure core (plan W6).

Three layers of verification:

  check_build()   — hard invariants: exactly 99 cards, singleton outside
                    basics, every card inside the commander's color
                    identity, land count in the playable range, the
                    castability gate passed, and quota audit (slot counts
                    match the profile, tolerating shortfalls the builder
                    explicitly warned about).  Any failure here is a bug.

  deck_census()   — per-deck quota counts (from the breakdown), the unit
                    of comparison against human decks.

  range_check()   — soft sanity: census values should fall inside the
                    range human decks actually occupy.  Human decks
                    validate the template, they are not the target — so a
                    miss here is a report line, and only the harness
                    decides whether to fail on it.

DB access, golden-set orchestration, and human-deck statistics live in
services/ingest/scripts/eval_harness.py.
"""

from __future__ import annotations

from .builder import MAX_LANDS, MIN_LANDS, BuildResult, assess_win_path
from .profile import CompositionProfile

_WUBRG_C = set("WUBRGC")


def _mdfc_spell_faces(result: BuildResult) -> int:
    """Spell-slot modal-DFC land faces — excludes MDFCs placed in land slots."""
    land_slot_names = set(result.breakdown.get("nonbasic_land", []))
    return sum(
        1 for c in result.deck
        if c.get("is_mdfc_land") and not c["is_land"] and c["name"] not in land_slot_names
    )


def deck_census(result: BuildResult) -> dict[str, int]:
    """Quota counts for one built deck, keyed like the profile quotas.

    Spell-front MDFCs placed in *land slots* count as lands (#143).
    """
    br = result.breakdown
    land_slot_names = set(br.get("nonbasic_land", []))
    lands = sum(
        1 for c in result.deck if c["is_land"] or c["name"] in land_slot_names
    )
    forced_nonland = sum(
        1 for c in result.deck if c["name"] in br.get("forced", []) and not c["is_land"]
    )
    return {
        "lands": lands,
        "ramp": len(br.get("ramp", [])) + forced_nonland,
        "draw": len(br.get("draw_engine", [])) + len(br.get("draw_spell", [])),
        "spot_removal": len(br.get("spot_removal", [])),
        "sweepers": len(br.get("sweeper", [])),
        "protection": len(br.get("protection", [])),
        # Forced wincons (#141) occupy theme slots — count them as theme.
        "theme": len(br.get("theme", [])) + len(br.get("filler", []))
        + len(br.get("wincon", [])),
    }


def check_build(
    profile: CompositionProfile,
    result: BuildResult,
    identity: set[str] | frozenset[str],
    ci_by_id: dict[str, set[str]] | None = None,
) -> list[str]:
    """Hard invariants; returns human-readable failures (empty = pass).

    ci_by_id — independent card_id → color identity map (from the DB), so
    color legality is verified against source data rather than trusting
    the pool filters that built the deck.
    """
    failures: list[str] = []
    deck = result.deck

    if len(deck) != profile.deck_size:
        failures.append(
            f"deck has {len(deck)} cards, expected {profile.deck_size}"
        )

    nonbasic_ids = [c["id"] for c in deck if not c["is_basic"]]
    if len(nonbasic_ids) != len(set(nonbasic_ids)):
        dupes = {i for i in nonbasic_ids if nonbasic_ids.count(i) > 1}
        failures.append(f"singleton violation: {len(dupes)} duplicated nonbasics")

    if ci_by_id is not None:
        identity_set = set(identity)
        for card in deck:
            ci = set(ci_by_id.get(card["id"], set())) & _WUBRG_C - {"C"}
            if not ci <= identity_set:
                failures.append(
                    f"color identity violation: {card['name']} ({''.join(sorted(ci))}) "
                    f"outside {''.join(sorted(identity_set)) or 'C'}"
                )

    census = deck_census(result)
    # MDFC land credit (#143) legitimately lowers real land count, never
    # below MIN_LANDS.
    lands_floor = max(MIN_LANDS, profile.lands.count - _mdfc_spell_faces(result) // 2)
    if not lands_floor <= census["lands"] <= MAX_LANDS:
        failures.append(
            f"lands {census['lands']} outside [{lands_floor}, {MAX_LANDS}]"
        )

    if not result.gate_passed:
        failures.append(
            f"castability gate failed: "
            f"P={result.goldfish.p_commander_by_go_live:.2f} < {result.gate:.2f}"
        )

    # Win-path audit (#141): the deck needs a credible way to close games —
    # dedicated finishers, a combat-win strategy (voltron/counters/anthem/
    # infect), combat mass, or drain density.  A miss is tolerated only
    # when the builder warned (color identities with no reachable path).
    spells = [c for c in deck if not c["is_land"]]
    win_ok, how = assess_win_path(spells, profile.signals)
    if not win_ok and not any("no win path" in w for w in result.warnings):
        failures.append(f"win-path audit failed ({how})")

    failures += quota_audit(profile, result, census)
    return failures


def quota_audit(
    profile: CompositionProfile,
    result: BuildResult,
    census: dict[str, int] | None = None,
) -> list[str]:
    """Slot counts must match the profile unless the builder said otherwise.

    Tolerated deviations must be *documented in warnings*: pool-exhausted
    shortfalls, and theme slots the feedback loop converted to lands.
    Silent deviations are failures.
    """
    census = census or deck_census(result)
    failures: list[str] = []

    def _exhausted(label: str) -> bool:
        return any(f"{label} pool exhausted" in w for w in result.warnings)

    expected = {
        "ramp": profile.ramp.count,
        "draw": profile.draw.count,
        "spot_removal": profile.spot_removal.count,
        "sweepers": profile.sweepers.count,
        "protection": profile.protection.count,
    }
    warn_label = {  # census key → warning label used by the builder
        "draw": "draw",  # builder warns per sub-pool; accept either
        "spot_removal": "spot_removal",
    }
    for key, want in expected.items():
        got = census[key]
        if got == want:
            continue
        label = warn_label.get(key, key)
        if got < want and (_exhausted(label) or _exhausted(key) or
                           any("pool exhausted" in w for w in result.warnings)):
            continue  # shortfall, but the builder said so
        failures.append(f"quota mismatch: {key} = {got}, profile says {want}")

    # Theme slots may legitimately shrink: pool shortfall (warned) or the
    # feedback loop converting theme to lands (also warned).
    extra_lands = census["lands"] - profile.lands.count
    want_theme = profile.theme.count - max(0, extra_lands)
    if census["theme"] < want_theme and not any(
        "pool exhausted" in w or "castability gate" in w for w in result.warnings
    ):
        failures.append(
            f"quota mismatch: theme = {census['theme']}, expected ≥ {want_theme}"
        )
    return failures


def range_check(
    census: dict[str, int],
    human_stats: dict[str, dict[str, float]],
    margin: int = 2,
) -> list[str]:
    """Soft sanity vs human deck distributions.

    human_stats: metric → {"min": …, "max": …, ...} computed from imported
    decks.  A value outside [min − margin, max + margin] is reported.
    """
    notes: list[str] = []
    for metric, stats in human_stats.items():
        if metric not in census:
            continue
        lo, hi = stats["min"] - margin, stats["max"] + margin
        v = census[metric]
        if not lo <= v <= hi:
            notes.append(
                f"{metric} = {v} outside human range "
                f"[{stats['min']:.0f}, {stats['max']:.0f}] ± {margin}"
            )
    return notes
