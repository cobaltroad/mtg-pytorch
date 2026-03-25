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

TRIBAL_PATTERNS: list[tuple[str, str, str]] = [
    ("tribal_elf",    "Tribal: Elf",             tribal_sql("elf")),
    ("oracle_elf",    "Oracle mention: Elf",      oracle_mention_sql("elf|elves")),
]
