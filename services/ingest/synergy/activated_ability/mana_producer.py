import re

from regex_utils import p

# Patterns that identify creatures (or permanents) with mana-producing activated
# abilities.  Each key becomes a trigger_event row in card_abilities so that
# _family_sql("mana_producer") can select the full producer set without
# enumerating oracle-text LIKE chains in SQL.
#
# Key breakdown:
#   mana_tap      — explicit {T}: Add {symbol} (Llanowar Elves, Birds of Paradise)
#   mana_add      — "add … mana" phrasing without a tap symbol (Selvala, Priest of Titania)
#   mana_ability  — oracle text uses the rules term "mana ability" (Tyvar the Bellicose)

PATTERNS: list[tuple[str, str, re.Pattern]] = [
    ("mana_tap",     "Mana ability: tap for mana",  p(r"\{t\}[^.]*add \{")),
    ("mana_add",     "Mana ability: add mana",       p(r"add (?:one |an amount of |that much )?mana")),
    ("mana_ability", "Mana ability: rules term",     p(r"\bmana ability\b")),
]
