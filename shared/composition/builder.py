"""Composition builder — assemble a legal 99 from a profile and ranked pools.

Layers 3–4 of docs/composition-first-plan.md.  Pure Python: the caller
supplies candidate pools already ranked (heuristics for the W3 baseline,
model scores from W4 onward) and already filtered to the commander's color
identity; this module owns quota enforcement, the mana base, and the
goldfish feedback loop.

Canonical card dict (built from cards ⋈ card_facts):

    id           str
    name         str
    mv           int
    pips         dict[str, int]
    hybrid       list[list[str]]
    is_land      bool
    is_basic     bool
    produces     list[str]        (cards.produced_mana ∩ WUBRG + C)
    etb_tapped   str | None
    is_fetch     bool
    roles        set[str]         pool memberships ('ramp', 'draw_engine', …)

Assembly order
--------------
1. forced includes (Sol Ring, Arcane Signet, Command Tower — configurable)
2. quota fill: ramp (≤ profile max MV) → draw engines/spells → spot removal
   → sweepers → protection → theme (curve-target aware)
3. mana base: nonbasics by quality score, then basics allocated to satisfy
   per-color Karsten source minimums before pip-census proportionality
4. goldfish; while the castability gate fails, convert a theme slot into a
   land (≤ MAX_FEEDBACK_ITERATIONS)

The castability gate is MV-scaled (calibrated against ideal quota decks
under the goldfisher's pessimistic 1-mana-per-source model — see
goldfish.py).  It exists to catch broken mana bases, not to promise a
turn-N commander.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .goldfish import GoldfishResult, simulate
from .profile import CompositionProfile

log = logging.getLogger(__name__)

_WUBRG = "WUBRG"

MAX_FEEDBACK_ITERATIONS = 3
MAX_LANDS = 40
MIN_LANDS = 33  # MDFC land credit never pushes real lands below this

# Win-path audit (#141): a deck must have a credible way to close games.
# Any ONE of these satisfies it:
WINCON_MIN = 2        # dedicated finishers (wincon pool), or
BIG_CREATURE_POWER = 5
BIG_CREATURE_MIN = 6  # this many power-5+ bodies = a beatdown plan, or
DRAIN_MIN = 6         # this many ping/lifeloss triggers = attrition, or
                      # a COMBAT_WIN_KEYS strategy (voltron/counters/anthem/
                      # infect — the theme itself is the win path).


def assess_win_path(spells: list[dict], signals: list[str]) -> tuple[bool, str]:
    """Does this spell selection have a credible way to win?

    Returns (satisfied, how).  Used by the builder to decide whether to
    force finishers, and by evaluation.check_build to verify the result.
    Cards must already carry their 'wincon'/'drain' role tags.
    """
    from .profile import COMBAT_WIN_KEYS

    strategy = set(signals) & COMBAT_WIN_KEYS
    if strategy:
        return True, f"strategy win path ({', '.join(sorted(strategy))})"
    finishers = sum(1 for c in spells if "wincon" in c.get("roles", set()))
    if finishers >= WINCON_MIN:
        return True, f"{finishers} dedicated finishers"
    big = sum(
        1 for c in spells if c.get("power", 0) >= BIG_CREATURE_POWER
    )
    if big >= BIG_CREATURE_MIN:
        return True, f"combat mass ({big} power-{BIG_CREATURE_POWER}+ creatures)"
    drains = sum(1 for c in spells if "drain" in c.get("roles", set()))
    if drains >= DRAIN_MIN:
        return True, f"drain density ({drains} ping/lifeloss sources)"
    return False, f"finishers={finishers} big={big} drains={drains}"

#: P(commander cast by max(go_live, MV)) floor, by commander MV and colored
#: pip count.  Calibrated so a sound mana base passes and a 20-land pile
#: fails; pessimism of the goldfish model grows with MV (no draw spells,
#: every source = 1 mana), and each colored pip beyond the first is a real
#: reliability cost no mana base fully recovers (Atraxa is harder to cast
#: than Wilhelt at any land count).
def castability_floor(mv: int, colored_pips: int = 1) -> float:
    if mv <= 2:
        base = 0.85
    elif mv <= 4:
        base = 0.80
    elif mv == 5:
        base = 0.70
    else:
        base = max(0.35, 0.60 - 0.05 * (mv - 6))  # a 10-drop is never T10-reliable
    if colored_pips >= 5:
        # Pip-dense tail (Niv-Mizzet, Parun: {U}{U}{U}{R}{R}{R}) measures
        # 0.33–0.37 under the goldfish model with a sound 37-land base —
        # extra headroom so Monte Carlo noise doesn't straddle the gate.
        base -= 0.05
    return max(0.30, base - 0.05 * max(0, colored_pips - 1))


@dataclass
class BuildResult:
    deck: list[dict]                       # 99 entries; basics repeat
    breakdown: dict[str, list[str]]        # slot → card names
    basic_counts: dict[str, int]           # color → number of basics
    goldfish: GoldfishResult
    gate: float
    gate_passed: bool
    iterations: int
    warnings: list[str] = field(default_factory=list)
    theme_density: dict | None = None  # attached by DB-side evaluators
    win_path: str = ""  # how the win-path audit was satisfied (#141)


# ── Land quality ──────────────────────────────────────────────────────────────


def land_quality(card: dict, identity: set[str]) -> float:
    """Heuristic quality of a nonbasic land for this color identity.

    Colors produced within the identity dominate; entering untapped is
    worth about one color.  A fetch with no produced_mana finds a basic of
    any deck color (#144), so it scores like a dual-plus in multicolor.
    Spell-front MDFC land faces (#143) come online tapped but carry a
    flexibility bonus — the card is also a spell when the land isn't needed.
    """
    produces = set(card.get("produces") or [])
    is_spell_face = card.get("is_mdfc_land") and not card.get("is_land")
    if card.get("is_fetch") and not produces:
        produces = set(identity)
    on_color = len(produces & identity)
    tapped = card.get("etb_tapped")
    untapped_bonus = (
        0.0 if is_spell_face  # MDFC backs come online next turn
        else 1.0 if tapped == "untapped"
        else 0.6 if tapped == "conditional"
        else 0.0
    )
    fetch_bonus = 0.8 if card.get("is_fetch") and len(identity) >= 2 else 0.0
    mdfc_bonus = 0.5 if is_spell_face else 0.0
    return on_color * 2.0 + untapped_bonus + fetch_bonus + mdfc_bonus


# ── Internal helpers ──────────────────────────────────────────────────────────


def _take(pool: list[dict], n: int, chosen: set[str], pred=None) -> list[dict]:
    """First n pool cards not yet chosen (pools arrive ranked best-first)."""
    out: list[dict] = []
    for card in pool:
        if len(out) >= n:
            break
        if card["id"] in chosen or card["is_land"]:
            continue
        if pred and not pred(card):
            continue
        out.append(card)
        chosen.add(card["id"])
    return out


def _curve_bucket(mv: int, targets) -> int:
    for t in targets:
        if mv <= t.max_mv:
            return t.max_mv
    return targets[-1].max_mv


# ── Public API ────────────────────────────────────────────────────────────────


def build_deck(
    profile: CompositionProfile,
    pools: dict[str, list[dict]],
    land_pool: list[dict],
    basics: dict[str, dict],
    forced: list[dict] | None = None,
    goldfish_games: int = 500,
    seed: int = 0,
    gate_relax: float = 0.0,
) -> BuildResult:
    """Assemble a 99-card deck.

    pools      — ranked, color-legal candidates per quota:
                 'ramp', 'draw_engine', 'draw_spell', 'spot_removal',
                 'sweeper', 'protection', 'theme'
    land_pool  — color-legal nonbasic lands (any order; ranked here)
    basics     — color letter → basic-land card dict
    forced     — auto-includes; counted against their quota (ramp/land)
    gate_relax — subtract from the castability floor when the goldfisher
                 is known to underestimate this commander (e.g. cost
                 reduction in its own text); caller documents why
    """
    identity = set(profile.color_identity)
    warnings: list[str] = []
    chosen: set[str] = set()
    breakdown: dict[str, list[str]] = {}
    spells: list[dict] = []

    # 1. Forced includes — split into land/ramp/other buckets.
    forced = forced or []
    forced_lands = [c for c in forced if c["is_land"]]
    forced_ramp = [c for c in forced if not c["is_land"] and "ramp" in c.get("roles", set())]
    for c in forced:
        chosen.add(c["id"])
    spells += forced_ramp
    breakdown["forced"] = [c["name"] for c in forced]

    # 2. Quota fill.
    ramp = forced_ramp + _take(
        pools.get("ramp", []),
        profile.ramp.count - len(forced_ramp),
        chosen,
        pred=lambda c: c["mv"] <= profile.ramp.max_mv,
    )
    draw_e = _take(pools.get("draw_engine", []), profile.draw.engines, chosen)
    draw_s = _take(pools.get("draw_spell", []), profile.draw.spells, chosen)
    spot = _take(pools.get("spot_removal", []), profile.spot_removal.count, chosen)
    sweep = _take(pools.get("sweeper", []), profile.sweepers.count, chosen)
    prot = _take(pools.get("protection", []), profile.protection.count, chosen)

    for label, cards in [("ramp", ramp[len(forced_ramp):]), ("draw_engine", draw_e),
                         ("draw_spell", draw_s), ("spot_removal", spot),
                         ("sweeper", sweep), ("protection", prot)]:
        breakdown[label] = [c["name"] for c in cards]
        spells += cards
    for label, quota, got in [
        ("ramp", profile.ramp.count, len(ramp)),
        ("draw", profile.draw.count, len(draw_e) + len(draw_s)),
        ("spot_removal", profile.spot_removal.count, len(spot)),
        ("sweepers", profile.sweepers.count, len(sweep)),
        ("protection", profile.protection.count, len(prot)),
    ]:
        if got < quota:
            warnings.append(f"{label} pool exhausted: {got}/{quota}")

    # Theme fill, curve-aware: first pass respects per-bucket capacity after
    # infrastructure; second pass relaxes if the pool can't fit the shape.
    # Diminishing returns: cards carrying `theme_keys` are soft-capped per
    # key so the 8th sac outlet loses its slot to the 1st counters payoff —
    # a linear scorer can't express saturation, so the builder does.
    theme_quota = profile.theme.count
    capacity = {t.max_mv: t.count for t in profile.curve_targets}
    for c in spells:
        b = _curve_bucket(c["mv"], profile.curve_targets)
        capacity[b] = capacity.get(b, 0) - 1
    all_keys = {k for c in pools.get("theme", []) for k in c.get("theme_keys", ())}
    key_cap = max(3, -(-theme_quota // len(all_keys)) + 2) if all_keys else 0
    key_counts: dict[str, int] = {}
    theme: list[dict] = []
    for relax in (False, True):
        if len(theme) >= theme_quota:
            break
        for card in pools.get("theme", []):
            if len(theme) >= theme_quota:
                break
            if card["id"] in chosen or card["is_land"]:
                continue
            b = _curve_bucket(card["mv"], profile.curve_targets)
            if not relax and capacity.get(b, 0) <= 0:
                continue
            keys = card.get("theme_keys") or set()
            if not relax and keys and all(key_counts.get(k, 0) >= key_cap for k in keys):
                continue  # every sub-theme this card serves is saturated
            capacity[b] = capacity.get(b, 0) - 1
            for k in keys:
                key_counts[k] = key_counts.get(k, 0) + 1
            theme.append(card)
            chosen.add(card["id"])
    if len(theme) < theme_quota:
        warnings.append(f"theme pool exhausted: {len(theme)}/{theme_quota}")
    breakdown["theme"] = [c["name"] for c in theme]
    spells += theme

    # Theme shortfall backfills from leftover interaction/draw/ramp pools
    # ("goodstuff" filler) — a signal-less commander gets a generically
    # sound deck, never a 65-land pile.
    shortfall = theme_quota - len(theme)
    if shortfall > 0:
        filler: list[dict] = []
        for pool_name in ("spot_removal", "draw_engine", "draw_spell",
                          "protection", "ramp", "sweeper"):
            filler += _take(pools.get(pool_name, []), shortfall - len(filler), chosen)
            if len(filler) >= shortfall:
                break
        breakdown["filler"] = [c["name"] for c in filler]
        spells += filler
        theme += filler  # filler is cuttable by the mana-base feedback loop too
        if filler:
            warnings.append(f"backfilled {len(filler)} theme slots from staple pools")

    # Win-path audit (#141): a deck can satisfy every quota and still have
    # no way to win.  Tag wincon-pool and drain-pool members, then check
    # the four win paths (strategy / finishers / combat mass / drain
    # density).  Only when NONE holds are the lowest-ranked theme cards
    # swapped for finishers — a voltron, counters, anthem, stompy, or
    # aristocrats deck is left untouched.  Forced wincons are NOT added to
    # the cuttable theme list — the mana-base feedback loop may not remove
    # them.
    wincon_pool = pools.get("wincon", [])
    wincon_ids = {c["id"] for c in wincon_pool}
    drain_ids = {c["id"] for c in pools.get("drain", [])}
    for c in spells:
        if c["id"] in wincon_ids:
            c.setdefault("roles", set()).add("wincon")
        if c["id"] in drain_ids:
            c.setdefault("roles", set()).add("drain")

    win_ok, win_path = assess_win_path(spells, profile.signals)
    forced_wincons: list[dict] = []
    if not win_ok:
        have_wincons = sum(1 for c in spells if "wincon" in c.get("roles", set()))
        for cand in wincon_pool:
            if have_wincons >= WINCON_MIN:
                break
            if cand["id"] in chosen or cand["is_land"]:
                continue
            cut = next(
                (t for t in reversed(theme) if "wincon" not in t.get("roles", set())),
                None,
            )
            if cut is None:
                break
            theme.remove(cut)
            spells.remove(cut)
            chosen.discard(cut["id"])
            for slot in ("filler", "theme"):
                if cut["name"] in breakdown.get(slot, []):
                    breakdown[slot].remove(cut["name"])
                    break
            cand.setdefault("roles", set()).add("wincon")
            spells.append(cand)
            chosen.add(cand["id"])
            forced_wincons.append(cand)
            have_wincons += 1
        win_ok, win_path = assess_win_path(spells, profile.signals)
        if not win_ok:
            warnings.append(f"no win path: {win_path} — wincon pool exhausted")
    breakdown["wincon"] = [c["name"] for c in forced_wincons]

    # 3 + 4. Mana base with goldfish feedback: theme slots convert to lands
    # while the castability gate fails.
    lands_target = profile.lands.count

    # MDFC land credit (#143): spell-slot modal DFCs with a land face act as
    # partial lands — every two free one real land slot for more spells.
    mdfc_spells = sum(1 for c in spells if c.get("is_mdfc_land") and not c["is_land"])
    mdfc_credit = min(mdfc_spells // 2, lands_target - MIN_LANDS)
    if mdfc_credit > 0:
        extra: list[dict] = []
        for pool_name in ("theme", "spot_removal", "draw_engine", "draw_spell",
                          "protection", "ramp"):
            extra += _take(pools.get(pool_name, []), mdfc_credit - len(extra), chosen)
            if len(extra) >= mdfc_credit:
                break
        if extra:
            lands_target -= len(extra)
            spells += extra
            theme += extra  # cuttable by the feedback loop
            breakdown["theme"] = breakdown.get("theme", []) + [c["name"] for c in extra]
            warnings.append(
                f"credited {mdfc_spells} MDFC land faces as {len(extra)} land slots"
            )
    total_pips = sum(r.pips for r in profile.pip_requirements)
    gate = max(0.0, castability_floor(profile.commander_mv, total_pips) - gate_relax)
    if gate_relax:
        warnings.append(f"castability gate relaxed by {gate_relax:.2f} (caller-documented)")
    check_turn = max(profile.go_live_turn, profile.commander_mv)
    ranked_lands = sorted(
        (c for c in land_pool if c["id"] not in chosen),
        key=lambda c: -land_quality(c, identity),
    )

    result: GoldfishResult | None = None
    iterations = 0
    while True:
        deck, basic_counts, nonbasic_names = _mana_base(
            profile, spells, forced_lands, ranked_lands, basics, lands_target, warnings
        )
        result = simulate(
            deck,
            profile.commander_mv,
            {c: r.pips for c, r in _pip_map(profile).items()},
            check_turn,
            games=goldfish_games,
            seed=seed,
        )
        iterations += 1
        if result.p_commander_by_go_live >= gate or iterations > MAX_FEEDBACK_ITERATIONS:
            break
        if lands_target >= MAX_LANDS or not theme:
            break  # gate warning added after the loop
        cut = theme.pop()  # lowest-ranked theme/filler card becomes a land
        spells.remove(cut)
        chosen.discard(cut["id"])
        for slot in ("filler", "theme"):  # filler cuts first (it's appended last)
            if cut["name"] in breakdown.get(slot, []):
                breakdown[slot].remove(cut["name"])
                break
        lands_target += 1
        log.info("goldfish %.2f < %.2f — cutting %s for a land (%d lands)",
                 result.p_commander_by_go_live, gate, cut["name"], lands_target)

    breakdown["nonbasic_land"] = nonbasic_names
    if result.p_commander_by_go_live < gate:
        warnings.append(
            f"castability gate unmet at {result.p_commander_by_go_live:.2f} "
            f"(floor {gate:.2f}) after {iterations} iterations, {lands_target} lands"
        )
    elif iterations > 1:
        warnings.append(f"mana base widened to {lands_target} lands to pass the castability gate")

    assert len(deck) == 99, f"built {len(deck)} cards, expected 99"
    return BuildResult(
        deck=deck,
        breakdown=breakdown,
        basic_counts=basic_counts,
        goldfish=result,
        gate=gate,
        gate_passed=result.p_commander_by_go_live >= gate,
        iterations=iterations,
        warnings=warnings,
        win_path=win_path,
    )


def _pip_map(profile: CompositionProfile):
    return {r.color: r for r in profile.pip_requirements}


def _mana_base(
    profile: CompositionProfile,
    spells: list[dict],
    forced_lands: list[dict],
    ranked_lands: list[dict],
    basics: dict[str, dict],
    lands_target: int,
    warnings: list[str],
) -> tuple[list[dict], dict[str, int], list[str]]:
    """Nonbasics by quality, then basics to Karsten minimums, then census."""
    identity = [c for c in profile.color_identity if c in _WUBRG]

    # Nonbasic cap: multicolor decks lean on duals; monocolor mostly wants
    # basics plus a few utility lands.
    nonbasic_cap = 8 if len(identity) <= 1 else 20
    nonbasics = forced_lands + [
        c for c in ranked_lands[: max(0, nonbasic_cap - len(forced_lands))]
    ]
    nonbasics = nonbasics[:lands_target]
    # Basics absorb both the remaining land budget and any spell-pool
    # shortfall, so the deck always totals exactly 99.
    basics_needed = 99 - len(spells) - len(nonbasics)

    # Per-color source count from nonbasics.  A fetch with no produced_mana
    # counts toward every identity color — it finds whichever basic the
    # hand is missing (#144).
    def _is_source(land: dict, color: str) -> bool:
        if color in (land.get("produces") or []):
            return True
        return bool(land.get("is_fetch")) and not land.get("produces")

    sources = {c: sum(1 for l in nonbasics if _is_source(l, c)) for c in identity}

    per_color = {c: 0 for c in identity}
    if identity and basics_needed > 0:
        # Priority 1: commander castability minimums (Karsten).
        minimums = {
            c: max(0, req.sources - sources.get(c, 0))
            for c, req in _pip_map(profile).items()
            if c in identity
        }
        remaining = basics_needed
        for c in sorted(minimums, key=minimums.get, reverse=True):
            add = min(minimums[c], remaining)
            per_color[c] += add
            remaining -= add
        unmet = {c: minimums[c] - per_color[c] for c in minimums if minimums[c] > per_color[c]}
        if unmet:
            warnings.append(
                "commander pip minimums unreachable with basics alone: "
                + ", ".join(f"{c} short {n}" for c, n in unmet.items())
            )
        # Priority 2: pip census proportionality for what's left.
        census = {c: 0 for c in identity}
        for s in spells:
            for c, n in (s.get("pips") or {}).items():
                if c in census:
                    census[c] += n
        total = sum(census.values()) or len(identity)
        for c in identity:
            per_color[c] += round(remaining * (census[c] if sum(census.values()) else 1) / total)
        drift = basics_needed - sum(per_color.values())
        order = sorted(identity, key=lambda c: -census[c])
        i = 0
        while drift != 0 and order:
            per_color[order[i % len(order)]] += 1 if drift > 0 else -1
            drift += -1 if drift > 0 else 1
            i += 1

    deck = list(spells) + nonbasics
    basic_counts: dict[str, int] = {}
    for c, n in per_color.items():
        if n > 0 and c in basics:
            basic_counts[c] = n
            deck += [basics[c]] * n
    if not identity and basics_needed > 0:  # colorless commander
        basic_counts["C"] = basics_needed
        if "C" in basics:
            deck += [basics["C"]] * basics_needed

    return deck, basic_counts, [l["name"] for l in nonbasics if l not in forced_lands]
