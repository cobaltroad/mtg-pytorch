"""Utility-land staple SQL — non-mana-producing lands with Commander value.

Covers three sub-populations:
  fetch_lands       — sacrifice to search the library for a specific land type
                      (Verdant Catacombs, Bloodstained Mire, Windswept Heath,
                      Terramorphic Expanse, Evolving Wilds, Fabled Passage)
                      Matched by: search your library + any basic land type
                      or the word "basic land"
  hand_size         — remove the hand-size ceiling (Reliquary Tower,
                      Thought Vessel, Library of Leng)
                      Matched by: "no maximum hand size"
  graveyard_hate    — exile graveyards at instant speed or on ETB (Bojuka Bog,
                      Scavenger Grounds, Burial Grounds)
                      Matched by: "exile" + "graveyard" in a land

Cards that also tap for colored mana (e.g. Command Tower) are primarily
classified under manabase.py.  Overlap between the two pools is harmless —
the caller unions into a set, so duplicates are automatically deduplicated.

Color identity filtering (card_ci ⊆ commander_ci) is applied by the caller.
Fetch lands typically have colorless identity (legal in any deck); graveyard-
hate lands are usually colorless or a single color.

RATE reflects the approximate share of a 99-card Commander deck devoted to
non-basic utility lands.
"""

from __future__ import annotations

RATE: float = 0.06

SQL: str = (
    "type_line ILIKE '%%Land%%'"
    " AND ("
    # fetch lands: search library and put a land onto the battlefield
    "  ("
    "    oracle_text ILIKE '%%search your library%%'"
    "    AND ("
    "      oracle_text ILIKE '%%basic land%%'"
    "      OR oracle_text ILIKE '%%Plains%%'"
    "      OR oracle_text ILIKE '%%Island%%'"
    "      OR oracle_text ILIKE '%%Swamp%%'"
    "      OR oracle_text ILIKE '%%Mountain%%'"
    "      OR oracle_text ILIKE '%%Forest%%'"
    "    )"
    "  )"
    "  OR"
    # hand-size removal (Reliquary Tower, Thought Vessel — Thought Vessel is not a land
    # but the pattern is land-specific here)
    "  oracle_text ILIKE '%%no maximum hand size%%'"
    "  OR"
    # graveyard hate on a land (Bojuka Bog, Scavenger Grounds)
    "  (oracle_text ILIKE '%%exile%%' AND oracle_text ILIKE '%%graveyard%%')"
    " )"
)
