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

RATE: float = 0.12

# Land ramp regex covers all five basic land types so that Farseek
# ("Plains, Island, Swamp, or Forest"), Three Visits ("Forest"), and
# Skyshroud Claim ("Forest") are all captured alongside Cultivate
# ("basic land card").
SQL: str = (
    "("
    # mana rocks: artifacts with {T}: Add ...
    "  (type_line ILIKE '%%Artifact%%'"
    "   AND oracle_text ~* '\\{T\\}.*[Aa]dd'"
    "   AND type_line NOT ILIKE '%%Land%%')"
    "  OR"
    # land ramp: non-creature spells that search the library for a land
    "  (oracle_text ILIKE '%%search your library%%'"
    "   AND ("
    "     oracle_text ILIKE '%%basic land%%'"
    "     OR oracle_text ILIKE '%%Plains%%'"
    "     OR oracle_text ILIKE '%%Island%%'"
    "     OR oracle_text ILIKE '%%Swamp%%'"
    "     OR oracle_text ILIKE '%%Mountain%%'"
    "     OR oracle_text ILIKE '%%Forest%%'"
    "   )"
    "   AND type_line NOT ILIKE '%%Land%%'"
    "   AND type_line NOT ILIKE '%%Creature%%')"
    "  OR"
    # mana dorks: creatures with {T}: Add ...
    "  (type_line ILIKE '%%Creature%%'"
    "   AND oracle_text ~* '\\{T\\}.*[Aa]dd')"
    ")"
)
