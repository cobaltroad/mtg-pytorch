import re

from regex_utils import p

# Patterns that identify cards which encourage or enable attacking.  Each key
# becomes a trigger_event row in card_abilities so that
# _family_sql("combat_tricks") can select the full combat-enabler set without
# enumerating oracle-text LIKE chains in SQL.
#
# Key breakdown:
#   evasion_enabler     — grants a keyword that makes creatures hard to block
#                         (flying, menace, fear, intimidate, shadow, horsemanship, skulk)
#   unblockable_enabler — "can't be blocked" (unconditional evasion)
#   damage_enabler      — grants a keyword that affects how combat damage is dealt
#                         (first strike, double strike, deathtouch, lifelink, trample)
#   vigilance_enabler   — grants vigilance (attacker doesn't tap)
#   all_attackers_pump  — blanket +N/+N to all attacking creatures
#   single_attacker_pump — targeted +N/+N to one attacking creature

PATTERNS: list[tuple[str, str, re.Pattern]] = [
    # Flying, menace, shadow, fear, intimidate, horsemanship, skulk — keywords that
    # let creatures attack without being fully blocked.
    ("evasion_enabler", "Grants an evasive keyword ability",
     p(r"(?:has|have|gains?)\s+(?:flying|menace|fear|intimidate|shadow|horsemanship|skulk)\b")),

    # "can't be blocked" — oracle phrasing for unconditional evasion.
    ("unblockable_enabler", "Grants unblockable",
     p(r"can't be blocked")),

    # First/double strike, deathtouch, lifelink, trample — keywords that determine
    # how combat damage is dealt or who survives.
    ("damage_enabler", "Grants damage-dealing keyword ability",
     p(r"(?:has|have|gains?)\s+(?:first strike|double strike|deathtouch|lifelink|trample)\b")),

    # Vigilance — lets a creature attack without tapping.
    ("vigilance_enabler", "Grants vigilance",
     p(r"(?:has|have|gains?)\s+vigilance\b")),

    # "attacking creatures get +N/+N" — blanket pump for the whole attack.
    ("all_attackers_pump", "Grants all attacking creatures a power/toughness boost",
     p(r"(?:attacking creatures?|each attacking creature)(?:\s+you control)?\s+get \+")),

    # "target attacking creature gets +N/+N" — single-target pump spell/ability.
    ("single_attacker_pump", "Grants a single attacking creature a power/toughness boost",
     p(r"target attacking creature(?:\s+you control)?\s+gets? \+")),
]