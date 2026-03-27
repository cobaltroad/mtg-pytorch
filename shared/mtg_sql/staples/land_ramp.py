from __future__ import annotations

SQL: str = (
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
)