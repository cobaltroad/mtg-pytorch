"""CompositionProfile — deterministic quota derivation per commander (Layer 2).

Every knob is *derived* from properties of the commander (mana value, pips,
color identity, decompose signals) — never learned, never a bare constant
without a rationale.  The profile is computed before any card selection and
is explainable by construction: every quota carries a ``because`` string
that flows through to the API/UI.

All tunable constants live in this module, next to their rationale.  If a
number needs changing, change it here and the explanation with it.

Inputs come from data Layer 1 already stores:
  * mana value / pips        — cards.cmc + card_facts.pips
  * color identity           — cards.color_identity
  * decompose signal keys    — stages/decompose.py ORACLE_PATTERNS keys
                               (card_abilities rows with source='decompose')
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .karsten import DEFAULT_THRESHOLD, required_sources

DECK_SIZE = 99  # non-commander cards

# ── Signal families (keys from stages/decompose.py ORACLE_PATTERNS) ──────────

#: The commander is the win condition and will be removed on sight.
VOLTRON_KEYS = frozenset({"equipment_matters", "cast_trigger_aura_equipment"})

#: The deck goes wide; opposing sweepers hurt more than our own help.
GO_WIDE_KEYS = frozenset({
    "token_generator",
    "creature_token_generator",
    "artifact_token_generator",
    "token_trigger",
    "weenie_matters",
})

# ── Quota model ───────────────────────────────────────────────────────────────


@dataclass
class Quota:
    count: int
    because: str


@dataclass
class RampQuota(Quota):
    max_mv: int = 3
    max_mv_because: str = ""


@dataclass
class DrawQuota(Quota):
    engines: int = 0
    spells: int = 0


@dataclass
class CurveTarget:
    max_mv: int  # bucket upper bound (99 = no bound)
    count: int


@dataclass
class PipRequirement:
    color: str
    pips: int
    by_turn: int
    sources: int  # colored sources the 99 must contain


@dataclass
class CompositionProfile:
    commander_name: str
    commander_mv: int
    color_identity: list[str]
    go_live_turn: int
    go_live_because: str
    lands: Quota
    ramp: RampQuota
    draw: DrawQuota
    spot_removal: Quota
    sweepers: Quota
    protection: Quota
    theme: Quota
    curve_targets: list[CurveTarget] = field(default_factory=list)
    pip_requirements: list[PipRequirement] = field(default_factory=list)

    def slot_total(self) -> int:
        return (
            self.lands.count
            + self.ramp.count
            + self.draw.count
            + self.spot_removal.count
            + self.sweepers.count
            + self.protection.count
            + self.theme.count
        )

    def as_dict(self) -> dict:
        """JSON-ready form for the API/UI."""
        return {
            "commander": self.commander_name,
            "commander_mv": self.commander_mv,
            "color_identity": self.color_identity,
            "go_live_turn": {"turn": self.go_live_turn, "because": self.go_live_because},
            "quotas": {
                "lands": vars(self.lands),
                "ramp": vars(self.ramp),
                "draw": vars(self.draw),
                "spot_removal": vars(self.spot_removal),
                "sweepers": vars(self.sweepers),
                "protection": vars(self.protection),
                "theme": vars(self.theme),
            },
            "curve_targets": [vars(c) for c in self.curve_targets],
            "pip_requirements": [vars(p) for p in self.pip_requirements],
        }


# ── Curve shapes ──────────────────────────────────────────────────────────────
# Spell-slot mana curves by commander MV, as shares of 63 reference slots
# (evolved from services/trainer/deck_builder.py CURVE_BUCKET_MV*).  Scaled
# to the actual spell count (99 − lands) at derivation time.

_CURVE_SHAPES: dict[int, list[tuple[int, int]]] = {
    2: [(1, 9), (2, 17), (3, 17), (4, 12), (5, 5), (99, 3)],
    3: [(1, 9), (2, 18), (3, 13), (4, 14), (5, 5), (99, 4)],
    4: [(1, 6), (2, 20), (3, 19), (4, 10), (5, 4), (99, 4)],
    5: [(1, 4), (2, 16), (3, 20), (4, 12), (5, 6), (99, 5)],
    6: [(1, 2), (2, 14), (3, 20), (4, 15), (5, 5), (99, 7)],
}


def _curve_targets(commander_mv: int, spell_slots: int) -> list[CurveTarget]:
    shape = _CURVE_SHAPES[min(max(commander_mv, 2), 6)]
    total_share = sum(n for _, n in shape)
    targets = [
        CurveTarget(max_mv=mv, count=round(n * spell_slots / total_share))
        for mv, n in shape
    ]
    # Rounding drift lands on the largest bucket so counts sum exactly.
    drift = spell_slots - sum(t.count for t in targets)
    max(targets, key=lambda t: t.count).count += drift
    return targets


# ── Derivation ────────────────────────────────────────────────────────────────


def derive_profile(
    commander_name: str,
    mana_value: float | int,
    pips: dict[str, int],
    color_identity: list[str],
    decompose_keys: set[str] | frozenset[str] = frozenset(),
    threshold: float = DEFAULT_THRESHOLD,
) -> CompositionProfile:
    """Derive the full quota profile for one commander.

    ``decompose_keys`` are the ORACLE_PATTERNS keys that fire on the
    commander's oracle text (empty set = vanilla / signal-less commander).
    """
    mv = int(round(mana_value))
    keys = frozenset(decompose_keys)
    n_signals = len(keys)
    is_voltron = bool(keys & VOLTRON_KEYS)
    is_go_wide = bool(keys & GO_WIDE_KEYS)

    # Go-live: when the deck expects its commander in play.  Expensive
    # commanders plan to ramp it out a turn ahead of curve; cheap ones just
    # cast it on curve (turn 2 floor — turn-1 commanders still spend the
    # first land drop).
    if mv >= 4:
        go_live = mv - 1
        go_live_because = f"commander costs {mv}; the deck ramps it out a turn early"
    else:
        go_live = max(2, mv)
        go_live_because = f"commander costs {mv}; cast on curve, no acceleration needed"

    # Ramp count scales with commander cost; ramp above commander_mv − 2
    # cannot accelerate the commander (a 2-mana rock turns a 4-drop into a
    # turn-3 play; a 3-mana rock does not), clamped to [2, 3] so cheap
    # commanders still get generic 2-MV ramp and big-mana decks keep the
    # Cultivate tier.
    ramp_count = 6 if mv <= 2 else 8 if mv == 3 else 10 if mv == 4 else 12
    ramp_max_mv = min(max(mv - 2, 2), 3)
    ramp = RampQuota(
        count=ramp_count,
        because=f"commander costs {mv}: enough acceleration to go live on turn {go_live}",
        max_mv=ramp_max_mv,
        max_mv_because=(
            f"ramp above {ramp_max_mv} MV can't come down before the commander needs it"
        ),
    )

    # Lands: 38 baseline for a ramp-less list, credited 1 land per 4 cheap
    # ramp pieces (they substitute for land drops), floored/capped to the
    # playable Commander range.
    lands_count = min(max(38 - ramp_count // 4, 33), 40)
    lands = Quota(
        count=lands_count,
        because=f"38 baseline − {ramp_count // 4} credit for {ramp_count} ramp pieces",
    )

    # Draw: ~10 pieces keeps a 4-player game's hand stocked.  Faster decks
    # (early go-live) empty their hand sooner and need recurring engines
    # more than one-shot refills.
    engines = 6 if go_live <= 3 else 5
    draw = DrawQuota(
        count=10,
        because="~10 card-advantage pieces keeps a multiplayer hand stocked",
        engines=engines,
        spells=10 - engines,
    )

    # Interaction: meta-neutral defaults.  Three opponents, but 1-for-1
    # answers can't cover everything — 7 flexible spot answers.
    spot = Quota(count=7, because="meta-neutral: 3 opponents, spot answers for must-kill threats")
    sweeper_count = 2 if is_go_wide else 3
    sweepers = Quota(
        count=sweeper_count,
        because=(
            "deck goes wide — its own sweepers cut against the plan"
            if is_go_wide
            else "board wipes reset games the deck is losing"
        ),
    )

    # Protection scales with how much of the deck routes through the
    # commander.  Voltron commanders are the win condition; multi-signal
    # engine commanders get removed on sight; vanilla value commanders can
    # shrug off removal.
    if is_voltron:
        protection = Quota(6, "voltron signals: the commander is the win condition")
    elif n_signals >= 3:
        protection = Quota(
            5, f"engine commander: {n_signals} distinct signals route through it"
        )
    elif n_signals >= 1:
        protection = Quota(3, "commander contributes an engine piece worth protecting")
    else:
        protection = Quota(2, "incidental-value commander; cheap insurance only")

    # Theme gets whatever scarcity remains — this remainder IS the
    # deckbuilding discipline the old pipeline lacked.
    reserved = (
        lands.count + ramp.count + draw.count + spot.count + sweepers.count + protection.count
    )
    theme = Quota(
        count=DECK_SIZE - reserved,
        because=f"remainder after {reserved} infrastructure slots",
    )

    # Per-color castability requirements for the commander itself at its
    # go-live turn.  Hybrid symbols in commander costs are rare enough to
    # ignore here; the goldfisher (W3) simulates them exactly.
    pip_reqs = [
        PipRequirement(
            color=color,
            pips=count,
            by_turn=go_live,
            sources=required_sources(go_live, count, DECK_SIZE, threshold),
        )
        for color, count in sorted(pips.items())
        if color in "WUBRG" and count > 0
    ]

    return CompositionProfile(
        commander_name=commander_name,
        commander_mv=mv,
        color_identity=list(color_identity),
        go_live_turn=go_live,
        go_live_because=go_live_because,
        lands=lands,
        ramp=ramp,
        draw=draw,
        spot_removal=spot,
        sweepers=sweepers,
        protection=protection,
        theme=theme,
        curve_targets=_curve_targets(mv, DECK_SIZE - lands.count),
        pip_requirements=pip_reqs,
    )
