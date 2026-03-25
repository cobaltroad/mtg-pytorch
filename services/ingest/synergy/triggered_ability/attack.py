import re

from regex_utils import p

PATTERNS: list[tuple[str, str, re.Pattern]] = [
    ("attack",          "Attack trigger",       p(r"whenever .{1,30} attacks?")),
    ("attack_phase",    "Attack phase trigger", p(r"at the beginning of (?:each )?combat(?: on your turn)?")),
    ("keyword_raid",    "Keyword: Raid",        p(r"(if|unless) you attacked (with a creature)? this turn")),
    ("keyword_exalted", "Keyword: Exalted",     p(r"whenever a creature you control attacks alone"))
]