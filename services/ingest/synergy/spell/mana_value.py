# SQL-based mana value filters — selected by CMC column rather than oracle text.
# Used as producer SQL in commander_mechanics.py for commanders whose payoff
# scales with the mana value of cards in the deck (e.g. Yuriko) or that
# specifically reward cheap spells (e.g. Edric, Sram).
#
# Key breakdown:
#   high_mv — CMC ≥ 6: maximises Yuriko-style "reveal and deal damage = MV"
#              triggers; also correct for Eldrazi / big-spell commanders.
#   low_mv  — CMC ≤ 2: weenie / storm / cEDH efficiency package;
#              correct for Edric, Sram, ISA, low-to-the-ground aggro.

PATTERNS: dict[str, str] = {
    "high_mv": "cmc >= 6",
    "low_mv":  "cmc <= 2",
}
