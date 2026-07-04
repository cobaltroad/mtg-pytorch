"""Castability math — how many colored sources a pip requirement demands.

Frank Karsten's consistency articles answer: "to cast a spell with N pips of
a color on curve at turn T, how many sources of that color must the deck
play?"  Rather than hardcoding his published tables, we compute the same
quantity from first principles so it works for any deck size and threshold.

Model
-----
P(castable) = P(at least `pips` sources of the color are among the cards
seen by turn `turn`), hypergeometric over the full deck.

    cards_seen(turn) = 7 + turn        (multiplayer Commander: every player
                                        draws on their first turn)

Simplifications vs Karsten's model (documented, deliberate):
  * no mulligans and no conditioning on hitting land drops — his tables
    condition on having `turn` lands in play, we look at raw cards seen.
    The two agree within ±1 source at typical Commander numbers.
  * one color at a time — multi-color joint availability is handled by the
    Monte Carlo goldfisher (plan W3), not closed form.

The default threshold is 0.90 ("castable on curve in 90% of games"), the
consistency bar Karsten's tables use.
"""

from __future__ import annotations

from functools import lru_cache
from math import comb

DEFAULT_THRESHOLD = 0.90
COMMANDER_DECK_SIZE = 99


def cards_seen(turn: int, opening_hand: int = 7) -> int:
    """Cards seen by a player's turn `turn` in multiplayer Commander."""
    return opening_hand + turn


@lru_cache(maxsize=None)
def prob_at_least(deck_size: int, sources: int, seen: int, need: int) -> float:
    """P(≥ `need` of the `sources` special cards appear in `seen` draws).

    Straight hypergeometric tail; complement over the (small) miss counts.
    """
    if need <= 0:
        return 1.0
    if sources < need or seen < need:
        return 0.0
    total = comb(deck_size, seen)
    miss = sum(
        comb(sources, k) * comb(deck_size - sources, seen - k)
        for k in range(min(need, seen))
    )
    return 1.0 - miss / total


def castable_prob(
    sources: int,
    turn: int,
    pips: int,
    deck_size: int = COMMANDER_DECK_SIZE,
) -> float:
    """P(the deck has drawn ≥ `pips` sources of the color by `turn`)."""
    return prob_at_least(deck_size, sources, cards_seen(turn), pips)


def required_sources(
    turn: int,
    pips: int,
    deck_size: int = COMMANDER_DECK_SIZE,
    threshold: float = DEFAULT_THRESHOLD,
) -> int:
    """Smallest number of colored sources meeting `threshold` castability.

    required_sources(turn=3, pips=2) → sources of that color the 99 needs
    for a reliable turn-3 {X}{C}{C} cast.  Returns deck_size if even a
    deck of nothing but sources can't reach the threshold (over-constrained
    pip counts at very early turns).
    """
    for sources in range(pips, deck_size + 1):
        if castable_prob(sources, turn, pips, deck_size) >= threshold:
            return sources
    return deck_size
