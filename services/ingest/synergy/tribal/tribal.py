def tribal_sql(tribe: str) -> str:
    """Return a SQL WHERE fragment matching cards of *tribe* or changelings."""
    return (
        f"(type_line ILIKE '%%{tribe}%%'"
        f" OR lower(oracle_text) LIKE '%%changeling%%')"
    )


def oracle_mention_sql(pattern: str) -> str:
    """Return a SQL WHERE fragment matching cards whose oracle text matches *pattern*.

    *pattern* is a Postgres regular expression (e.g. ``'elf|elves'``).
    """
    return f"lower(oracle_text) ~ '\\m({pattern})\\M'"


# Tribal membership patterns resolved via SQL rather than oracle-text regex.
#
# Tuple shape: (tribe_key, label, where_sql)
#   tribe_key — used as trigger_event in card_abilities (e.g. "tribal_elf")
#   label     — human-readable name, used as ability_name
#   where_sql — SQL WHERE fragment from tribal_sql(); selects matching cards

# (tribe, oracle_regex) — tribe is the MTG subtype word (always singular in
# type_line); oracle_regex handles irregular plurals for oracle text matching.
TRIBES: list[tuple[str, str]] = [
    ("elf",       "elf|elves"),
    ("dragon",    "dragon|dragons"),
    ("zombie",    "zombie|zombies"),
    ("vampire",   "vampire|vampires"),
    ("eldrazi",   "eldrazi"),
    ("human",     "human|humans"),
    ("dinosaur",  "dinosaur|dinosaurs"),
    ("goblin",    "goblin|goblins"),
    ("angel",     "angel|angels"),
    ("pirate",    "pirate|pirates"),
    ("wizard",    "wizard|wizards"),
    ("assassin",  "assassin|assassins"),
    ("merfolk",   "merfolk"),
    ("cat",       "cat|cats"),
    ("sliver",    "sliver|slivers"),
    ("wolf",      "wolf|wolves"),
]

TRIBAL_PATTERNS: list[tuple[str, str, str]] = []
for _tribe, _oracle_pattern in TRIBES:
    _label = _tribe.title()
    TRIBAL_PATTERNS.append((f"tribal_{_tribe}", f"Tribal: {_label}",          tribal_sql(_tribe)))
    TRIBAL_PATTERNS.append((f"oracle_{_tribe}", f"Oracle mention: {_label}",  oracle_mention_sql(_oracle_pattern)))
