import re

from regex_utils import p

# Cards that trigger off creatures entering the battlefield.
# These are the CONSUMERS of a commander that generates ETB events (e.g. a
# commander that blinks, bounces, or repeatedly recasts creatures).  tag.py
# writes these trigger_event values into card_abilities so that
# compute_textmatch_synergy can build producer→consumer edges.
#
# Key breakdown:
#   creature_etb            — generic: any creature entering (Panharmonicon,
#                             Purphoros, God of the Forge)
#   creature_etb_you_control — restricted to your own creatures entering
#                             (Impact Tremors, Anointed Procession payoffs)
#   creature_etb_self       — the card itself entering (most ETB creatures;
#                             doubled by Panharmonicon / Conjurer's Closet)
#   nontoken_creature_etb   — specifically non-token creatures entering
#                             (Mentor of the Meek, Beast Whisperer payoffs)

PATTERNS: list[tuple[str, str, re.Pattern]] = [
    ("nontoken_creature_etb",    "Nontoken creature ETB trigger",        p(r"whenever (?:another )?nontoken creature enters")),
    ("creature_etb_you_control", "Creature ETB trigger (you control)",   p(r"whenever (?:another )?creature you control enters")),
    ("creature_etb",             "Creature ETB trigger (any creature)",  p(r"whenever (?:a |one or more |another )?creatures? enters?")),
    ("creature_etb_self",        "Creature ETB trigger (self)",          p(r"when (?:this creature|~ |it) enters")),
]

# Direct oracle_text SQL for the creature_etb_payoff deck key.  Each sub-key maps
# to its own WHERE fragment so stages/mechanics.py can write fine-grained role rows.
CREATURE_ETB_SQL: dict[str, str] = {
    "nontoken_creature_etb":    "oracle_text ~* 'whenever .{0,20}nontoken creature enters'",
    "creature_etb_you_control": "oracle_text ~* 'whenever .{0,20}creature you control enters'",
    "creature_etb":             "oracle_text ~* 'whenever .{0,20}creatures? enters?'",
    "creature_etb_self":        "oracle_text ~* 'when (this creature|it) enters'",
}
SQL: str = "(" + " OR ".join(CREATURE_ETB_SQL.values()) + ")"
