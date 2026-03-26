# SQL-based color filters — selected by the colors TEXT[] column rather than
# oracle text.  Used as producer SQL in commander_mechanics.py for commanders
# whose cast trigger fires on spells of a specific color (e.g. Aragorn for
# multicolor, K'rrik for black spells, Chandra for red spells).
#
# Key breakdown:
#   spell_white     — cards with W in their colors array
#   spell_blue      — cards with U in their colors array
#   spell_black     — cards with B in their colors array
#   spell_red       — cards with R in their colors array
#   spell_green     — cards with G in their colors array
#   spell_colorless — cards with no colors (empty array); excludes lands

PATTERNS: dict[str, str] = {
    "spell_white":     "colors @> ARRAY['W']",
    "spell_blue":      "colors @> ARRAY['U']",
    "spell_black":     "colors @> ARRAY['B']",
    "spell_red":       "colors @> ARRAY['R']",
    "spell_green":     "colors @> ARRAY['G']",
    "spell_colorless": "colors = '{}' AND type_line NOT ILIKE '%%Land%%'",
}
