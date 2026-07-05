"""Interaction / protection staple SQL — responses and shields.

Covers four sub-populations:
  counterspells       — hard counters (Counterspell, Arcane Denial, Swan Song,
                        Negate, Dovin's Veto) and conditional counters
                        (Mystic Confluence, Sublime Epiphany)
                        Matched by: oracle_text ILIKE '%%counter target%%'
  hexproof grants     — instant-speed or triggered hexproof for your permanents
                        (Heroic Intervention, Swiftfoot Boots, Autumn's Veil)
                        Matched by: gain/gains hexproof
  indestructible      — instant-speed or triggered indestructible grants
                        (Heroic Intervention, Make a Stand, Semester's End)
                        Matched by: gain/gains indestructible
  shroud              — static shroud grants (Lightning Greaves, Asceticism,
                        Sylvan Safekeeper)
                        Matched by: oracle_text ILIKE '%%shroud%%'

Note: bounce is classified under removal.py since it answers permanents
rather than preventing spells from resolving.

Color identity filtering (card_ci ⊆ commander_ci) is applied by the caller.

RATE reflects the approximate share of a 99-card Commander deck devoted to
interaction and protection.
"""

from __future__ import annotations

RATE: float = 0.06

#: Counterspells only — the composition builder's spot-removal pool uses
#: this instead of the full SQL union, because the hexproof/indestructible/
#: shroud clauses below duplicate the dedicated protection pool
#: (protection.py) and were eating removal slots once popularity ranking
#: surfaced Swiftfoot Boots / Lightning Greaves to the top (#140).
COUNTERSPELLS: str = (
    "(oracle_text ILIKE '%%counter target%%' AND type_line NOT ILIKE '%%Land%%')"
)

SQL: str = (
    "("
    # counterspells (hard and conditional)
    "  oracle_text ILIKE '%%counter target%%'"
    "  OR"
    # hexproof grants (static, triggered, or instant-speed)
    "  oracle_text ILIKE '%%gain hexproof%%'"
    "  OR oracle_text ILIKE '%%gains hexproof%%'"
    "  OR oracle_text ILIKE '%%have hexproof%%'"
    "  OR oracle_text ILIKE '%%has hexproof%%'"
    "  OR"
    # indestructible grants
    "  oracle_text ILIKE '%%gain indestructible%%'"
    "  OR oracle_text ILIKE '%%gains indestructible%%'"
    "  OR oracle_text ILIKE '%%have indestructible%%'"
    "  OR oracle_text ILIKE '%%has indestructible%%'"
    "  OR"
    # shroud grants (Lightning Greaves, Asceticism, Sylvan Safekeeper)
    "  oracle_text ILIKE '%%shroud%%'"
    ")"
    " AND type_line NOT ILIKE '%%Land%%'"
)
