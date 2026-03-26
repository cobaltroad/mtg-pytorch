"""Draw-engine staple SQL — repeatable card advantage permanents.

Selects non-instant, non-sorcery permanents that draw cards via triggered
or triggered-at-upkeep/draw-step abilities:
  whenever … draw   — Rhystic Study, Mystic Remora, Niv-Mizzet, Skullclamp,
                       Consecrated Sphinx, Arcanis the Omnipotent
  at the beginning  — Phyrexian Arena, Sylvan Library, Well of Ideas,
                       Howling Mine, Alhammarret's Archive

Lands and one-shot instants/sorceries are excluded; the draw must reference
"card" to filter out incidental "draw step" reminder-text matches.

Note: draw-payoff commanders (Niv-Mizzet, The Locust God) will appear in
this pool and can become positives for unrelated commanders.  This is
acceptable — they are genuine draw engines regardless of their other roles.

Color identity filtering (card_ci ⊆ commander_ci) is applied by the caller.

RATE reflects the approximate share of a 99-card Commander deck devoted to
repeatable draw engines.
"""

from __future__ import annotations

RATE: float = 0.08

SQL: str = (
    "type_line NOT ILIKE '%%Instant%%'"
    " AND type_line NOT ILIKE '%%Sorcery%%'"
    " AND type_line NOT ILIKE '%%Land%%'"
    " AND oracle_text ILIKE '%%draw%%'"
    " AND oracle_text ILIKE '%%card%%'"
    " AND ("
    "   oracle_text ILIKE '%%whenever%%'"
    "   OR oracle_text ILIKE '%%at the beginning%%'"
    " )"
)
