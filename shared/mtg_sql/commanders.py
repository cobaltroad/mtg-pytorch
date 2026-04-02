"""Commander eligibility SQL fragments.

LEGAL_SQL — legality predicate only: all commander-format-legal cards.
TYPE_SQL  — type/text predicate:
              - Legendary Creature (any combination of supertypes, e.g.
                "Legendary Enchantment Creature" for Sythis)
              - oracle text "can be your commander" (e.g. planeswalkers with
                an explicit commander clause)
              - oracle text "isn't on the battlefield, it's a creature" (Grist,
                the Hunger Tide — a Legendary Planeswalker that is a creature
                everywhere except the battlefield)
            Legendary Planeswalkers are NOT included as a blanket category;
            only those with an explicit "can be your commander" line or a
            characteristic-defining creature ability qualify.
WHERE     — combined: legal + eligible type, suitable as a WHERE body.

Note: ILIKE patterns use %% so the strings are safe to embed in psycopg2
queries both with and without parameter substitution.
"""

from __future__ import annotations

LEGAL_SQL: str = "legalities->>'commander' = 'legal'"

TYPE_SQL: str = (
    "("
    "  (type_line ILIKE '%%Legendary%%' AND type_line ILIKE '%%Creature%%')"
    "  OR oracle_text ILIKE '%%can be your commander%%'"
    "  OR oracle_text ILIKE '%%isn_t on the battlefield%%it_s a%%creature%%'"
    ")"
)

WHERE: str = LEGAL_SQL + " AND " + TYPE_SQL
