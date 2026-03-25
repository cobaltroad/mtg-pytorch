# PRODUCER SQL: when a commander has cast_trigger_<type>, the deck needs cards
# that are spells of that type — they feed the commander's trigger.
# Keyed by the decompose.py pattern key → SQL WHERE body selecting the
# spell-type cards.
PATTERNS: dict[str, str] = {
    "spell_enchantment":     "type_line ILIKE '%%Enchantment%%' AND type_line NOT ILIKE '%%Land%%'",
    "spell_creature":        "type_line ILIKE '%%Creature%%'",
    "spell_artifact":        "type_line ILIKE '%%Artifact%%' AND type_line NOT ILIKE '%%Land%%'",
    "spell_instant_sorcery": "type_line ILIKE '%%Instant%%' OR type_line ILIKE '%%Sorcery%%'",
    "spell_historic":        (
        "type_line ILIKE '%%Artifact%%'"
        " OR type_line ILIKE '%%Legendary%%'"
        " OR type_line ILIKE '%%Saga%%'"
    ),
    "spell_aura_equipment": (
        "(type_line ILIKE '%%Enchantment%%' AND oracle_text ILIKE '%%Enchant %%')"
        " OR (type_line ILIKE '%%Artifact%%' AND oracle_text ILIKE '%%Equip%%')"
    ),
}
