import re


def p(pattern: str) -> re.Pattern:
    """Compile *pattern* as a case-insensitive regex."""
    return re.compile(pattern, re.IGNORECASE)
