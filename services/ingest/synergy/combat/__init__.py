import re

from .combat import PATTERNS as _combat_patterns

# Aggregated flat list for tag.py — same tuple shape as other synergy modules:
# (key, label, compiled re.Pattern)
COMBAT_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    *_combat_patterns,
]

# Family map: groups fine-grained keys under a single commander-mechanic key.
# "combat_tricks" collects every card that encourages or enables attacking —
# evasion grants, damage keywords, vigilance, pump spells — so that
# _family_sql("combat_tricks") returns the full combat-enabler set without
# enumerating individual keys at the call site.
PATTERNS: dict[str, list[str]] = {
    "combat_tricks": [key for key, _, _ in _combat_patterns],
}

__all__ = ["COMBAT_PATTERNS", "PATTERNS"]
