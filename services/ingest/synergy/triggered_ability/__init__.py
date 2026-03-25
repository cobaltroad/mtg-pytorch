import re

from .attack import TRIGGER_PATTERNS as _attack_patterns
from .counter import TRIGGER_PATTERNS as _counter_patterns

# Aggregated list of all trigger patterns.
# Tuple shape: (key, label, pattern)
#   key     — pattern key, used as trigger_event in card_abilities
#   label   — human-readable name, used as ability_name
#   pattern — compiled re.Pattern applied to oracle_text via pattern.search()
TRIGGER_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    *_attack_patterns,
    *_counter_patterns,
]

# Maps a commander-level mechanic family key to all trigger_event keys that
# belong to it.  When a commander needs "attack_trigger", the deck wants cards
# tagged with ANY of the patterns in the attack family.
#
# Convention: the family key matches the triggering module's filename stem
# suffixed with "_trigger" (attack.py → "attack_trigger").
PATTERN_FAMILIES: dict[str, list[str]] = {
    "attack_trigger":  [key for key, _, _ in _attack_patterns],
    "counter_trigger": [key for key, _, _ in _counter_patterns],
}

__all__ = ["TRIGGER_PATTERNS", "PATTERN_FAMILIES"]
