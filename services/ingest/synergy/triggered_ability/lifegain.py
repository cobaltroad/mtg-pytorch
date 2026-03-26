import re

from regex_utils import p

# Cards that trigger or scale off life being gained.
# These are the CONSUMERS of a commander that produces lifegain (e.g. Sythis,
# Oloro, Dina).  tag.py writes these trigger_event values into card_abilities
# so that compute_textmatch_synergy can build producer→consumer edges.

PATTERNS: list[tuple[str, str, re.Pattern]] = [
    ("lifegain_trigger",        "Lifegain trigger (you gain life)",     p(r"whenever you gain life")),
    ("lifegain_trigger_any",    "Lifegain trigger (any player)",        p(r"whenever (?:a player|an opponent) gains life")),
    ("lifegain_static",         "Lifegain static payoff",               p(r"for each (?:1 )?life (?:you gain|gained)")),
    ("lifegain_lifelink",       "Keyword: Lifelink",                    p(r"\blifelink\b")),
    ("lifegain_soul_warden",    "Soul Warden ETB lifegain",             p(r"whenever (?:another )?creature enters.{0,40}you gain")),
]
