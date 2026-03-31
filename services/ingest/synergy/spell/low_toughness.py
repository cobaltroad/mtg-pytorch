# SQL-based toughness filters — selected by the toughness column rather than
# oracle text.  toughness is stored as TEXT in the cards table to accommodate
# variable values (*, *+1, X), so comparisons use string equality.
#
# Key breakdown:
#   toughness_1 — creatures with exactly 1 toughness: dies to any damage source,
#                 making them reliable, low-friction death-trigger fodder.
#                 (Elvish Mystic, Birds of Paradise, Goblin Lackey, Mother of Runes,
#                  Viscera Seer, Zulaport Cutthroat, Blood Artist, Reassembling
#                  Skeleton)
#                 Correct for death-trigger commanders (Syr Konrad, Teysa Karlov,
#                 Ayara) who want creatures that die easily to any pinged or
#                 sacrificed.

PATTERNS: dict[str, str] = {
    "toughness_1": "type_line ILIKE '%%Creature%%' AND toughness = '1'",
}
