import re

from regex_utils import p

PATTERNS: list[tuple[str, str, re.Pattern]] = [
    ("attack",          "Attack trigger",       p(r"whenever .{1,30} attacks?")),
    ("attack_phase",    "Attack phase trigger", p(r"at the beginning of (?:each )?combat(?: on your turn)?")),
    ("keyword_raid",    "Keyword: Raid",        p(r"(if|unless) you attacked (with a creature)? this turn")),
    ("keyword_exalted", "Keyword: Exalted",     p(r"whenever a creature you control attacks alone"))
]

# Direct oracle_text SQL mirroring PATTERNS, one WHERE fragment per fine key,
# so stages/mechanics.py can write fine-grained role rows.  This dict was
# missing (unlike every sibling module), so the attack family had zero
# card_abilities coverage and every _family_sql('attack_trigger') consumer —
# notably trigger_doubling — silently matched nothing (issue #137).
ATTACK_SQL: dict[str, str] = {
    "attack":          "oracle_text ~* 'whenever .{1,30} attacks?'",
    "attack_phase":    "oracle_text ~* 'at the beginning of (each )?combat( on your turn)?'",
    "keyword_raid":    "oracle_text ~* '(if|unless) you attacked (with a creature )?this turn'",
    "keyword_exalted": "oracle_text ILIKE '%%whenever a creature you control attacks alone%%'",
}
SQL: str = "(" + " OR ".join(ATTACK_SQL.values()) + ")"