import re

from ._common import p

TRIGGER_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    ("proliferate",         "Keyword: Proliferate",             p(r"proliferate")),
    ("hardened_scales",     "Counter Replacement: Hardened Scales", p(r"if one or more .*counters would be put")),
    ("vorinclex",           "Counter Replacement: Vorinclex",    p(r"if you would put one or more .*counters")),
    ("scurry_oak",          "Counter Replacement: Scurry Oak",   p(r"whenever one or more \+1/\+1 counters are put on")),
    ("bramblewood_paragon", "Counter Anthem",                    p(r"each creature you control with a \+1/\+1 counter on it")),
    ("undergrowth_champion","Counter Check",                     p(r"while|if .* ha(s|d) a \+1/\+1 counter on it")),
]