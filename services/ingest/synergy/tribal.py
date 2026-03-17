"""Tribal synergy patterns and producer SQL fragments.

Generates three pattern/producer pairs for each tribe in TRIBES:
  * tribal_{tribe}_cast — "whenever you cast a {Tribe} spell"
  * tribal_{tribe}_etb  — "whenever a {Tribe} enters (the battlefield)"
  * tribal_{tribe}_lord — "(other) {Tribe}s you control get +1/+1" (static lord effect)

Changelings ('Changeling' = ANY(keywords)) count as every creature type
simultaneously, so they are included in the producer pool for all tribes.

Two cross-synergy overrides expand the default type_line-based producer pools:
  * Zombies — includes reanimation effects (a natural Zombie sub-theme).
  * Angels  — includes lifegain effects (the canonical Angel sub-theme).
"""

from __future__ import annotations

from .lifegain import LIFEGAIN_PRODUCER_SQL

# ── Tribe list ────────────────────────────────────────────────────────────────

# Major Commander tribes, in rough popularity order.
TRIBES: list[str] = [
    "Dragon", "Elf", "Zombie", "Vampire", "Eldrazi", "Human",
    "Dinosaur", "Goblin", "Angel", "Pirate", "Wizard", "Assassin",
    "Merfolk", "Cat", "Sliver", "Wolf",
]

# ── Dynamic pattern + producer generation ────────────────────────────────────

TRIGGER_PATTERNS: list[tuple[str, str, str]] = []
PRODUCER_MAP: dict[str, str] = {}

# SQL predicate: card is (or grants) every creature type simultaneously.
#   * Changeling keyword — Mothdust Changeling, Mirror Entity, etc.
#   * "is every creature type" in oracle text — Maskwood Nexus ("each creature
#     you control is every creature type"), Universal Automaton self-grants, etc.
ALL_TYPES_SQL: str = (
    "'Changeling' = ANY(keywords)"
    " OR lower(oracle_text) LIKE '%is every creature type%'"
)
_CHANGELING = ALL_TYPES_SQL  # alias kept for legacy cross-synergy blocks below

for _tribe in TRIBES:
    _t = _tribe.lower()
    _type_where = f"(lower(type_line) LIKE '%{_t}%' OR {_CHANGELING})"

    # Consumer patterns
    TRIGGER_PATTERNS.append((
        rf"when(ever)?\s+you cast (a |an )?{_tribe}",
        f"{_tribe} cast trigger",
        f"tribal_{_t}_cast",
    ))
    TRIGGER_PATTERNS.append((
        rf"when(ever)?\s+(a |another )?{_tribe}.{{0,20}}enters",
        f"{_tribe} ETB trigger",
        f"tribal_{_t}_etb",
    ))
    # Lord / anthem effect: "(other) {Tribe}s you control get +1/+1" — static ability.
    # Treated as a triggered-style consumer so the lord card pairs with tribe members.
    TRIGGER_PATTERNS.append((
        rf"(other )?{_tribe}s? (you control|you own).{{0,30}}(get|have|gain)",
        f"{_tribe} lord effect",
        f"tribal_{_t}_lord",
    ))

    # Producer: any card of that creature type (or a changeling)
    PRODUCER_MAP[f"tribal_{_t}_cast"] = _type_where
    PRODUCER_MAP[f"tribal_{_t}_etb"]  = _type_where
    PRODUCER_MAP[f"tribal_{_t}_lord"] = _type_where

# ── Cross-synergy overrides ───────────────────────────────────────────────────

# Zombies naturally pair with reanimation effects (graveyard recursion)
_zombie_reanimation = (
    f"(lower(type_line) LIKE '%zombie%' OR {_CHANGELING})"
    " OR lower(oracle_text) LIKE '%return target%creature%graveyard%battlefield%'"
    " OR lower(oracle_text) LIKE '%creature card from%graveyard%battlefield%'"
    " OR lower(oracle_text) LIKE '%put target%creature%graveyard%battlefield%'"
    " OR lower(oracle_text) LIKE '%unearth%'"
)
PRODUCER_MAP["tribal_zombie_cast"] = _zombie_reanimation
PRODUCER_MAP["tribal_zombie_etb"]  = _zombie_reanimation
PRODUCER_MAP["tribal_zombie_lord"] = _zombie_reanimation

# Angels naturally pair with lifegain effects
_angel_lifegain = (
    f"(lower(type_line) LIKE '%angel%' OR {_CHANGELING})"
    " OR " + LIFEGAIN_PRODUCER_SQL
)
PRODUCER_MAP["tribal_angel_cast"] = _angel_lifegain
PRODUCER_MAP["tribal_angel_etb"]  = _angel_lifegain
PRODUCER_MAP["tribal_angel_lord"] = _angel_lifegain
