import re

from .mana_producer import PATTERNS as _mana_producer_patterns

# Aggregated list of all activated-ability patterns.
# Same tuple shape as triggered_ability: (key, label, compiled re.Pattern)
ACTIVATED_ABILITY_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    *_mana_producer_patterns,
]

# Family map: groups fine-grained keys under a single commander-mechanic key
# so _family_sql("mana_producer") selects cards tagged with any mana pattern.
PATTERNS: dict[str, list[str]] = {
    "mana_producer": [key for key, _, _ in _mana_producer_patterns],
}

__all__ = ["ACTIVATED_ABILITY_PATTERNS", "PATTERNS"]
