"""Monte Carlo goldfisher — can this 99 actually cast its commander on time?

Simulates solitaire games (draws, land drops, ramp casts) and reports how
often the commander is castable by the profile's go-live turn.  This is the
feedback signal for the builder's mana-base loop and the primary evaluation
metric for generated decks (plan W3/W6).

Deck cards are plain dicts sharing the builder's canonical shape (see
``builder.py``); the fields the goldfisher reads:

    mv           int             mana value
    pips         dict[str, int]  strict colored pips
    hybrid       list[list[str]] flexible pip payment options
    is_land      bool
    produces     list[str]       colors of mana the permanent can make
    mana_output  int             mana per activation (default 1; Sol Ring 2,
                                 Thran Dynamo 3 — estimated from oracle text)
    etb_tapped   str | None      'always' | 'conditional' | 'untapped'
    roles        set[str]        pool memberships; 'ramp' is what matters here

Policy assumptions (deliberately simple, documented):
  * every player draws on turn 1 (multiplayer Commander rules)
  * land choice: untapped before tapped, missing commander colors first
  * 'conditional' tapped lands are played as untapped (check/fast/shock
    lands are built to enter untapped when the game matters)
  * ramp is cast greedily before the commander; mana rocks/dorks come
    online next turn; land-ramp spells put a tapped land into play
  * one spell resource line per turn — no discard, no interaction, an
    optimistic upper bound on the deck's own speed
  * no mulligans; keepable-hand rate (2–5 lands) is reported separately
"""

from __future__ import annotations

import random
from dataclasses import dataclass

_WUBRG = "WUBRG"


@dataclass
class GoldfishResult:
    p_commander_by_go_live: float
    avg_cast_turn: float  # among games that cast it within max_turns
    cast_rate: float      # fraction of games cast within max_turns
    keepable_rate: float  # opening hands with 2–5 lands
    games: int


def _pips_satisfied(
    pips: dict[str, int],
    hybrid: list[list[str]],
    sources: list[frozenset[str]],
    total_mana: int,
    mv: int,
) -> bool:
    """sources carries one entry per mana source: its producible colors."""
    """Greedy matching: can these sources pay the colored part of the cost?

    Each source pays at most one pip.  Strict pips are matched
    scarcest-color-first; hybrid symbols then take anything left (a hybrid
    option of generic/life always succeeds if raw mana is there).
    """
    if total_mana < mv:
        return False
    demand: list[str] = []
    for color, n in pips.items():
        if color in _WUBRG:
            demand += [color] * n
    # Scarcest color first so duals aren't wasted on easy pips.
    avail = list(sources)
    demand.sort(key=lambda c: sum(1 for s in avail if c in s))
    for color in demand:
        match = None
        # Prefer the narrowest source that can pay this pip.
        for i, s in enumerate(avail):
            if color in s and (match is None or len(s) < len(avail[match])):
                match = i
        if match is None:
            return False
        avail.pop(match)
    # Hybrid pips: colored option if a source remains, else generic/life
    # options make them payable by definition.
    for options in hybrid:
        colored = [o for o in options if o in _WUBRG]
        payer = next((i for i, s in enumerate(avail) if any(o in s for o in colored)), None)
        if payer is not None:
            avail.pop(payer)
        elif not any(o == "P" or o.isdigit() for o in options):
            return False
    return True


def _land_sort_key(card: dict, needed: set[str]) -> tuple:
    produces = set(card.get("produces") or [])
    hits_need = bool(produces & needed)
    untapped = card.get("etb_tapped") != "always"
    return (not hits_need, not untapped)  # False sorts first


def simulate(
    deck: list[dict],
    commander_mv: int,
    commander_pips: dict[str, int],
    go_live_turn: int,
    commander_hybrid: list[list[str]] | None = None,
    games: int = 500,
    max_turns: int = 12,
    seed: int = 0,
) -> GoldfishResult:
    """Run `games` solitaire simulations of `deck` (list of 99 card dicts)."""
    rng = random.Random(seed)
    hybrid = commander_hybrid or []
    needed_colors = {c for c in commander_pips if c in _WUBRG}

    cast_turns: list[int] = []
    keepable = 0

    for _ in range(games):
        library = deck[:]
        rng.shuffle(library)
        hand = [library.pop() for _ in range(7)]
        if 2 <= sum(1 for c in hand if c["is_land"]) <= 5:
            keepable += 1

        # One entry per mana source: (producible colors, mana per activation).
        battlefield: list[tuple[frozenset[str], int]] = []
        tapped_this_turn = 0                     # sources unavailable until next turn
        cast_turn = None

        for turn in range(1, max_turns + 1):
            tapped_this_turn = 0
            if library:
                hand.append(library.pop())

            # Land drop.
            lands = [c for c in hand if c["is_land"]]
            if lands:
                lands.sort(key=lambda c: _land_sort_key(c, needed_colors))
                land = lands[0]
                hand.remove(land)
                battlefield.append(
                    (frozenset(land.get("produces") or []), land.get("mana_output", 1))
                )
                if land.get("etb_tapped") == "always":
                    tapped_this_turn += 1

            def _available():
                usable = battlefield[: len(battlefield) - tapped_this_turn]
                return usable, sum(out for _, out in usable)

            _, mana = _available()

            # Greedy ramp before the commander comes down.
            ramp_in_hand = sorted(
                (c for c in hand if "ramp" in (c.get("roles") or set()) and not c["is_land"]),
                key=lambda c: c["mv"],
            )
            for card in ramp_in_hand:
                if card["mv"] > mana:
                    break
                hand.remove(card)
                mana -= card["mv"]
                if card.get("produces"):
                    battlefield.append(
                        (frozenset(card["produces"]), card.get("mana_output", 1))
                    )
                else:
                    # Land-ramp spell: a (tapped) land joins the battlefield.
                    battlefield.append((frozenset(needed_colors or {"C"}), 1))
                tapped_this_turn += 1  # new source comes online next turn

            available, total = _available()
            if cast_turn is None and _pips_satisfied(
                commander_pips, hybrid, [colors for colors, _ in available],
                total, commander_mv,
            ):
                cast_turn = turn
                break

        if cast_turn is not None:
            cast_turns.append(cast_turn)

    n_by_go_live = sum(1 for t in cast_turns if t <= go_live_turn)
    return GoldfishResult(
        p_commander_by_go_live=n_by_go_live / games,
        avg_cast_turn=(sum(cast_turns) / len(cast_turns)) if cast_turns else float(max_turns),
        cast_rate=len(cast_turns) / games,
        keepable_rate=keepable / games,
        games=games,
    )
