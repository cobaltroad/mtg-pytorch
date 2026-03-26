# Unlike the triggered_ability / activated_ability modules, these patterns are
# not oracle-text regexes — they are SQL WHERE bodies that query structural card
# columns (type_line, cmc, colors).  They serve as producer SQL in
# commander_mechanics.py: when a commander's decompose key identifies what kind
# of spell it wants, the matching entry here selects that card set from the DB.

from .type import PATTERNS as _type_patterns
from .mana_value import PATTERNS as _mana_value_patterns
from .color import PATTERNS as _color_patterns
from .cast_from_exile import PATTERNS as _cast_from_exile_patterns

PATTERNS: dict[str, str] = {
    **_type_patterns,
    **_mana_value_patterns,
    **_color_patterns,
    **_cast_from_exile_patterns,
}

__all__ = ["PATTERNS"]
