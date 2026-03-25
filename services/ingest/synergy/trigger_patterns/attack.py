import re

from regex_utils import p

TRIGGER_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    ("attack_trigger",       "Attack trigger",       p(r"whenever .s attacks?'")),
    ("attack_phase_trigger", "Attack phase trigger",  p(r"at the beginning of (combat on your turn|each combat)?")),
    ("keyword_raid",         "Keyword: Raid",         p(r"(if|unless) you attacked (with a creature)? this turn")),
]