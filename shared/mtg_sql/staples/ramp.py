"""Ramp staple SQL — mana acceleration for any Commander deck.

Covers three sub-populations:
  mana_rocks   — artifacts that tap to produce mana (Sol Ring, Arcane Signet, Mana Vault)
  land_ramp    — non-creature spells that fetch/put lands from library
                 (Cultivate, Kodama's Reach, Rampant Growth, Nature's Lore, Farseek)
  mana_dorks   — creatures that tap to produce mana
                 (Llanowar Elves, Birds of Paradise, Selvala)

Color identity filtering (card_ci ⊆ commander_ci) is applied by the caller;
this SQL returns all color-agnostic candidates.

RATE reflects the approximate share of a 99-card Commander deck devoted to ramp.
"""

from __future__ import annotations

from .mana_rocks import SQL as _MANA_ROCK_SQL
from .land_ramp import SQL as _LAND_RAMP_SQL
from .mana_dorks import SQL as _MANA_DORK_SQL

RATE: float = 0.12

# Land ramp regex covers all five basic land types so that Farseek
# ("Plains, Island, Swamp, or Forest"), Three Visits ("Forest"), and
# Skyshroud Claim ("Forest") are all captured alongside Cultivate
# ("basic land card").
SQL: str = (
    "("
    "  (" + _MANA_ROCK_SQL + ")"
    "  OR"
    "  (" + _LAND_RAMP_SQL + ")"
    "  OR"
    "  (" + _MANA_DORK_SQL + ")"
    ")"
)
