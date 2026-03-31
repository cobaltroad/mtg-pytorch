import re

from .attack import PATTERNS as _attack_patterns
from .counter import PATTERNS as _counter_patterns
from .lifegain import PATTERNS as _lifegain_patterns
from .draw import PATTERNS as _draw_patterns
from .creature_etb import PATTERNS as _creature_etb_patterns
from .sacrifice import PATTERNS as _sacrifice_patterns

# Aggregated list of all trigger patterns.
# Tuple shape: (key, label, pattern)
#   key     — pattern key, used as trigger_event in card_abilities
#   label   — human-readable name, used as ability_name
#   pattern — compiled re.Pattern applied to oracle_text via pattern.search()
TRIGGERED_ABILITY_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    *_attack_patterns,
    *_counter_patterns,
    *_lifegain_patterns,
    *_draw_patterns,
    *_creature_etb_patterns,
    *_sacrifice_patterns,
]

# Maps a commander-level mechanic family key to all trigger_event keys that
# belong to it.  When a commander needs "attack_trigger", the deck wants cards
# tagged with ANY of the patterns in the attack family.
#
# Convention: the family key matches the triggering module's filename stem
# suffixed with "_trigger" (attack.py → "attack_trigger").
PATTERNS: dict[str, list[str]] = {
    "attack_trigger":   [key for key, _, _ in _attack_patterns],
    "counter_trigger":  [key for key, _, _ in _counter_patterns],
    "lifegain_trigger": [key for key, _, _ in _lifegain_patterns],
    "draw_trigger":     [key for key, _, _ in _draw_patterns],
    "creature_etb":     [key for key, _, _ in _creature_etb_patterns],
    "sacrifice_fodder": [key for key, _, _ in _sacrifice_patterns],
}

__all__ = ["TRIGGERED_ABILITY_PATTERNS", "PATTERNS"]
