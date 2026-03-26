import re

from regex_utils import p

# Patterns that identify cards with sacrifice outlets — activated abilities
# that accept a creature or permanent as part of their cost.  Each key becomes
# a trigger_event row in card_abilities so that _family_sql("sac_outlet") can
# select the full outlet set without enumerating oracle-text LIKE chains in SQL.
#
# Key breakdown:
#   sac_outlet_creature  — "sacrifice a/another creature:" as activated cost
#                          (Viscera Seer, Ashnod's Altar, Goblin Bombardment)
#   sac_outlet_permanent — "sacrifice a/another permanent:" — broader outlets
#                          that accept any permanent (Grinding Station, Krark-Clan
#                          Ironworks, Phyrexian Tower)

PATTERNS: list[tuple[str, str, re.Pattern]] = [
    ("sac_outlet_creature",  "Sac outlet: sacrifice a creature",  p(r"sacrifice a(?:nother)? creature\s*:")),
    ("sac_outlet_permanent", "Sac outlet: sacrifice a permanent", p(r"sacrifice a(?:nother)? permanent\s*:")),
]
