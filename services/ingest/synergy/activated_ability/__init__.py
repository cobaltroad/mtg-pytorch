import re

from .mana_producer import ACTIVATED_PATTERNS as _mana_patterns

# Aggregated list of all activated-ability patterns.
# Same tuple shape as trigger_patterns: (key, label, compiled re.Pattern)
ACTIVATED_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    *_mana_patterns,
]

# Family map: groups fine-grained keys under a single commander-mechanic key
# so _family_sql("mana_producer") selects cards tagged with any mana pattern.
PATTERN_FAMILIES: dict[str, list[str]] = {
    "mana_producer": [key for key, _, _ in _mana_patterns],
}

__all__ = ["ACTIVATED_PATTERNS", "PATTERN_FAMILIES"]
