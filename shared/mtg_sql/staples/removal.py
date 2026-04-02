"""Removal staple SQL — targeted permanent elimination for any Commander deck.

Named sub-constants let callers select only the removal modes relevant to
their archetype.  ``SQL`` is the union of all modes for callers that want
any targeted removal regardless of how it works.

Mode breakdown
--------------
DESTROY  — "destroy target": creature goes to the graveyard, firing death
           triggers.  (Doom Blade, Murder, Generous Gift, Beast Within,
           Ravenous Chupacabra)

DAMAGE   — targeted damage or -X/-X that kills without exile: creature goes
           to the graveyard, firing death triggers.  (Lightning Bolt,
           Terminate, Gut Shot, Disfigure, Tragic Slip)

EXILE    — "exile target": permanent removal that bypasses the graveyard;
           death triggers do NOT fire.  (Swords to Plowshares, Path to
           Exile, Anguished Unmaking, Grasp of Fate)

BOUNCE   — return target to owner's hand: soft removal; death triggers do
           NOT fire.  (Cyclonic Rift, Into the Roil, Brazen Borrower,
           Vapor Snag)

Death-trigger commanders (Syr Konrad, Teysa Karlov) want DESTROY and DAMAGE
only — both send creatures to the graveyard.  Generic staple callers use SQL
(all modes) since they prioritise answering threats over graveyard synergy.

Color identity filtering (card_ci ⊆ commander_ci) is applied by the caller.

RATE reflects the approximate share of a 99-card Commander deck devoted to
targeted removal.
"""

from __future__ import annotations

RATE: float = 0.12

_NOT_LAND = "type_line NOT ILIKE '%%Land%%'"

DESTROY: str = (
    f"oracle_text ILIKE '%%destroy target%%'"
    f" AND {_NOT_LAND}"
)

DAMAGE: str = (
    "("
    # direct targeted damage — Lightning Bolt, Gut Shot, Terminate
    "  oracle_text ~* 'deals? [0-9X]+ damage to (?:target creature|any target)'"
    "  OR"
    # -X/-X until end of turn — Disfigure, Tragic Slip
    "  oracle_text ~* 'gets? -[0-9]+/-[0-9]+ until end of turn'"
    ")"
    f" AND {_NOT_LAND}"
)

EXILE: str = (
    f"oracle_text ILIKE '%%exile target%%'"
    # Exclude graveyard-hate / graveyard-cost cards.  Two templates appear:
    #   "exile target card from a graveyard"  (Shadowfeed — targeted GY hate)
    #   "exile a card from your graveyard"    (Stonerise Spirit — GY cost)
    # Both operate on the graveyard, not the battlefield, and should never
    # be positive peers of Swords to Plowshares / Path to Exile.
    f" AND oracle_text NOT ILIKE '%%exile%%card%%from%%graveyard%%'"
    f" AND {_NOT_LAND}"
)

BOUNCE: str = (
    "oracle_text ILIKE '%%return target%%'"
    " AND oracle_text ILIKE '%%owner%%s hand%%'"
    f" AND {_NOT_LAND}"
)

SQL: str = f"({DESTROY} OR {DAMAGE} OR {EXILE} OR {BOUNCE})"
