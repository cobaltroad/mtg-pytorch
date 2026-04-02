import re

from regex_utils import p

# Cards that trigger or scale off drawing cards.
# These are the CONSUMERS of a commander that produces card draw (e.g. Sythis,
# Edric, Niv-Mizzet).  tag.py writes these trigger_event values into
# card_abilities so that compute_textmatch_synergy can build producer→consumer
# edges.

PATTERNS: list[tuple[str, str, re.Pattern]] = [
    ("draw_trigger",            "Draw trigger (you draw)",              p(r"whenever you draw a card")),
    ("draw_trigger_any",        "Draw trigger (any player draws)",      p(r"whenever (?:a player|an opponent) draws")),
    ("draw_static",             "Draw static payoff",                   p(r"for each card (?:you draw|drawn)")),
    ("draw_replacement",        "Draw replacement effect",              p(r"if you would draw a card")),
    ("draw_first_card",         "Draw trigger (first card each turn)",  p(r"whenever you draw your first card")),
]

# Direct oracle_text SQL for the draw_trigger deck key — union of all patterns
# above as PostgreSQL WHERE fragments against the cards table.  Used by
# stages/mechanic_tags.py to tag draw-payoff cards without depending on
# card_abilities rows from tag_abilities.
SQL: str = (
    "(oracle_text ILIKE '%%whenever you draw a card%%'"
    " OR oracle_text ~* 'whenever (a player|an opponent) draws'"
    " OR oracle_text ~* 'for each card (you draw|drawn)'"
    " OR oracle_text ILIKE '%%if you would draw a card%%'"
    " OR oracle_text ILIKE '%%whenever you draw your first card%%')"
)
