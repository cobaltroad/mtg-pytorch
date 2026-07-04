"""Sweeper staple SQL — mass removal for any Commander deck.

Covers four board-wipe patterns:
  destroy/exile all    — classic board wipes (Wrath of God, Damnation,
                          Toxic Deluge, Martial Coup)
  damage to each       — pyroclasm-style sweepers (Blasphemous Act, Star of
                          Extinction, Chain Reaction)
  -X/-X to each        — power/toughness sweepers (Languish, Black Sun's
                          Zenith, Toxic Deluge toughness mode)
  mass bounce          — return all nonland permanents (Cyclonic Rift overload,
                          River's Rebuke, Evacuation)

The "destroy all" and "exile all" patterns are left intentionally broad so
that partial sweepers (e.g. "destroy all artifacts", "exile all creatures")
are also captured — narrower sweepers are still staples for the decks whose
color identity can run them.

Color identity filtering (card_ci ⊆ commander_ci) is applied by the caller.

RATE reflects the approximate share of a 99-card Commander deck devoted to
board wipes.
"""

from __future__ import annotations

RATE: float = 0.06

SQL: str = (
    "("
    "  oracle_text ILIKE '%%destroy all%%'"
    # "exile all" must name battlefield objects — bare '%%exile all%%'
    # matches library manipulation ("exile all other cards revealed this
    # way", Demonic Consultation)
    "  OR oracle_text ~* 'exile all (other )?(attacking |blocking |nonland )?(creature|permanent|artifact|enchantment|planeswalker|token)'"
    # pyroclasm-style: "deals N damage to each creature"
    "  OR oracle_text ~* 'deals? [0-9]+ damage to each creature'"
    # toughness reduction: "each creature gets -N/-N"
    "  OR oracle_text ~* 'each creature gets -[0-9]+/-[0-9]+'"
    # mass bounce (Cyclonic Rift overload, River's Rebuke)
    "  OR oracle_text ILIKE '%%return all nonland%%'"
    ")"
    " AND type_line NOT ILIKE '%%Land%%'"
)
