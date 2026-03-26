import re

from regex_utils import p

# Patterns that identify creatures (or permanents) with mana-producing activated
# abilities.  Each key becomes a trigger_event row in card_abilities so that
# _family_sql("mana_producer") can select the full producer set without
# enumerating oracle-text LIKE chains in SQL.
#
# Key breakdown:
#   mana_rock     — artifact (non-land) with {T}: Add {symbol}.  Anchored to
#                   the type_line so Llanowar Elves and basic lands are excluded.
#                   Covers Sol Ring, Arcane Signet, Talisman cycle, Signets, etc.
#                   search_text is "{type_line}\n{oracle_text}", so we use
#                   re.MULTILINE + re.IGNORECASE and anchor with ^ to match only
#                   when "Artifact" appears at the start of the type_line.
#   mana_tap      — explicit {T}: Add {symbol} on any permanent (Llanowar Elves,
#                   Birds of Paradise, and also caught by mana_rock for artifacts)
#   mana_add      — "add … mana" phrasing without a tap symbol (Selvala, Priest of Titania)
#   mana_ability  — oracle text uses the rules term "mana ability" (Tyvar the Bellicose)

PATTERNS: list[tuple[str, str, re.Pattern]] = [
    ("mana_rock",    "Mana rock: artifact tap for mana",
     re.compile(r"^artifact\b(?! land\b)[\s\S]{0,200}\{T\}[^.\n]*add (?:\{|one mana|mana)", re.IGNORECASE | re.MULTILINE)),
    ("mana_tap",     "Mana ability: tap for mana",  p(r"\{t\}[^.]*add \{")),
    ("mana_add",     "Mana ability: add mana",       p(r"add (?:one |an amount of |that much )?mana")),
    ("mana_ability", "Mana ability: rules term",     p(r"\bmana ability\b")),
]
