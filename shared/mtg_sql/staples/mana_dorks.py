from __future__ import annotations

SQL: str = (
    # mana dorks: creatures with {T}: Add ...
    "  (type_line ILIKE '%%Creature%%'"
    "   AND oracle_text ~* '\\{T\\}.*[Aa]dd')"
)