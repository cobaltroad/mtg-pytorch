"""Removal staple SQL — targeted permanent elimination for any Commander deck.

Covers three removal modes:
  destroy/exile target — hard removal (Swords to Plowshares, Path to Exile,
                          Generous Gift, Beast Within, Ravenous Chupacabra,
                          Reclamation Sage)
  bounce target         — soft removal returning a permanent to hand
                          (Cyclonic Rift, Into the Roil, Unsummon)
  -X/-X until end       — targeted toughness reduction killing the creature
                          (Disfigure, Tragic Slip, Feast of Sanity)

Includes creatures with removal ETBs (e.g. Ravenous Chupacabra) since these
are valid removal pieces in Commander.

Color identity filtering (card_ci ⊆ commander_ci) is applied by the caller.

RATE reflects the approximate share of a 99-card Commander deck devoted to
targeted removal.
"""

from __future__ import annotations

RATE: float = 0.12

SQL: str = (
    "("
    # destroy/exile a single target
    "  oracle_text ILIKE '%%destroy target%%'"
    "  OR oracle_text ILIKE '%%exile target%%'"
    "  OR"
    # bounce: return a single target to its owner's hand
    "  (oracle_text ILIKE '%%return target%%'"
    "   AND oracle_text ILIKE '%%owner%%s hand%%')"
    "  OR"
    # targeted -X/-X until end of turn (Disfigure, Tragic Slip)
    "  oracle_text ~* 'gets? -[0-9]+/-[0-9]+ until end of turn'"
    ")"
    " AND type_line NOT ILIKE '%%Land%%'"
)
