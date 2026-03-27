"""Draw-spell staple SQL — one-shot card draw instants and sorceries.

Selects instants and sorceries whose oracle text draws one or more cards:
  Brainstorm, Ponder, Preordain, Night's Whisper, Painful Truths,
  Harmonize, Divination, Read the Bones, Frantic Search, Windfall, Wheel
  of Fortune, Treasure Cruise, Dig Through Time, etc.

Instants that draw as a rider (e.g. "Counter target spell. Draw a card.")
are also captured — incidental draw is still useful.

Color identity filtering (card_ci ⊆ commander_ci) is applied by the caller.

RATE reflects the approximate share of a 99-card Commander deck devoted to
one-shot draw spells.
"""

from __future__ import annotations

RATE: float = 0.06

SQL: str = (
    "("
    "  type_line ILIKE '%%Instant%%'"
    "  OR type_line ILIKE '%%Sorcery%%'"
    ")"
    " AND oracle_text ILIKE '%%draw%%'"
    " AND oracle_text ILIKE '%%card%%'"
    " AND type_line NOT ILIKE '%%Land%%'"
)
