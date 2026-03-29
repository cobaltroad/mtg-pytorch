"""Commander eligibility SQL fragments.

LEGAL_SQL — legality predicate only: all commander-format-legal cards.
TYPE_SQL  — type/text predicate: Legendary Creature, Legendary Planeswalker,
            or oracle text "can be your commander".
WHERE     — combined: legal + eligible type, suitable as a WHERE body.

Note: ILIKE patterns use %% so the strings are safe to embed in psycopg2
queries both with and without parameter substitution.
"""
from __future__ import annotations

LEGAL_SQL: str = "legalities->>'commander' = 'legal'"

TYPE_SQL: str = (
    "("
    "  type_line ILIKE '%%Legendary Creature%%'"
    "  OR type_line ILIKE '%%Legendary Planeswalker%%'"
    "  OR oracle_text ILIKE '%%can be your commander%%'"
    ")"
)

WHERE: str = LEGAL_SQL + " AND " + TYPE_SQL
