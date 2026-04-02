import re

from regex_utils import p

PATTERNS: list[tuple[str, str, re.Pattern]] = [
    ("proliferate",         "Keyword: Proliferate",             p(r"proliferate")),
    ("hardened_scales",     "Counter Replacement: Hardened Scales", p(r"if one or more .*counters would be put")),
    ("vorinclex",           "Counter Replacement: Vorinclex",    p(r"if you would put one or more .*counters")),
    ("scurry_oak",          "Counter Replacement: Scurry Oak",   p(r"whenever one or more \+1/\+1 counters are put on")),
    ("bramblewood_paragon", "Counter Anthem",                    p(r"each creature you control with a \+1/\+1 counter on it")),
    ("undergrowth_champion","Counter Check",                     p(r"(?:while|if) .* ha[sd] a \+1/\+1 counter on it")),
]

# Direct oracle_text SQL for the counter_trigger deck key — union of all patterns
# above as PostgreSQL WHERE fragments against the cards table.  Used by
# stages/mechanic_tags.py to tag counter-amplifier cards without depending on
# card_abilities rows from tag_abilities.
SQL: str = (
    "(oracle_text ILIKE '%%proliferate%%'"
    " OR oracle_text ~* 'if one or more .{0,20}counters would be put'"
    " OR oracle_text ~* 'if you would put one or more .{0,20}counters'"
    " OR oracle_text ~* 'whenever one or more [+]1/[+]1 counters are put on'"
    " OR oracle_text ~* 'each creature you control with a [+]1/[+]1 counter on it'"
    " OR oracle_text ~* '(while|if) .{0,30}ha[sd] a [+]1/[+]1 counter on it')"
)