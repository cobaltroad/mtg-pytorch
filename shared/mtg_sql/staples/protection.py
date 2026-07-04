"""Protection staple SQL — keeping your own pieces (usually the commander) alive.

Consumed by the composition builder (shared/composition/builder.py) to fill
the profile's protection quota.  Voltron and engine commanders get removed
on sight; these are the cards that blank the removal.

Mode breakdown
--------------
GRANT    — gives hexproof / shroud / indestructible / protection to your
           stuff.  (Swiftfoot Boots, Lightning Greaves, Heroic Intervention,
           Mother of Runes, Blossoming Defense, Darksteel Plate)

PHASE    — phases your permanents out; dodges everything including board
           wipes and exile.  (Teferi's Protection, Slip Out the Back,
           Guardian of Faith)

FLICKER  — exiles your own creature and returns it: removal fizzles, ETBs
           re-fire as a bonus.  (Ephemerate, Ghostly Flicker, Flickerwisp)

Color identity filtering (card_ci ⊆ commander_ci) is applied by the caller.

RATE reflects the approximate share of a 99-card Commander deck devoted to
protection in a commander-centric list; incidental-value commanders run
close to zero (see shared/composition/profile.py protection derivation).
"""

from __future__ import annotations

RATE: float = 0.05

_NOT_LAND = "type_line NOT ILIKE '%%Land%%'"

GRANT: str = (
    "("
    # up to 30 chars between verb and keyword, same sentence — catches
    # "has haste and shroud" (Lightning Greaves)
    "  oracle_text ~* '(gains?|has|have|gets?) [^.]{0,30}(hexproof|shroud|indestructible|protection from)'"
    "  OR oracle_text ~* 'you (gain|have) (hexproof|shroud)'"
    ")"
    f" AND {_NOT_LAND}"
)

PHASE: str = (
    "oracle_text ~* '(permanents?|creatures?|it) (you control )?phases? out'"
    f" AND {_NOT_LAND}"
)

FLICKER: str = (
    "oracle_text ~* 'exile (up to \\w+ )?target (creature|permanent)s? you control'"
    " AND oracle_text ~* 'return (it|that card|them|those cards) to the battlefield'"
    f" AND {_NOT_LAND}"
)

SQL: str = f"({GRANT} OR {PHASE} OR {FLICKER})"
