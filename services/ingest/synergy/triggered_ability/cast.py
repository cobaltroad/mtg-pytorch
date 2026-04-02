import re

from regex_utils import p

# Cards that trigger when a spell of a specific type is cast.
# These are the CONSUMERS of a commander that rewards casting spells of a given
# type (e.g. Sythis for enchantments, Beast Whisperer for creatures, Sram for
# auras/equipment).  tag.py writes these trigger_event values into card_abilities
# so that compute_textmatch_synergy can build producer→consumer edges.
#
# Key breakdown:
#   enchantment_cast     — triggers when an enchantment spell is cast
#                          (Sythis, Eidolon of Blossoms)
#   creature_cast        — triggers when a creature spell is cast
#                          (Beast Whisperer, Mentor of the Meek, Temur Ascendancy)
#   artifact_cast        — triggers when an artifact spell is cast
#                          (Jhoira's Familiar payoffs, Daretti)
#   instant_sorcery_cast — triggers when an instant or sorcery is cast
#                          (Guttersnipe, Murmuring Mystic, Talrand)
#   historic_cast        — triggers when a historic spell is cast (artifact,
#                          legendary, or Saga) (Jhoira, Teshar)
#   aura_equipment_cast  — triggers when an Aura, Equipment, or Vehicle spell
#                          is cast (Sram, Armory Paladin, Puresteel Paladin)

PATTERNS: list[tuple[str, str, re.Pattern]] = [
    ("enchantment_cast",     "Enchantment cast trigger",
     p(r"whenever .{0,20}cast (?:an? )?enchantment")),

    ("creature_cast",        "Creature cast trigger",
     p(r"whenever .{0,20}cast (?:a |an )?creature spell")),

    ("artifact_cast",        "Artifact cast trigger",
     p(r"whenever .{0,20}cast (?:an? )?artifact spell")),

    ("instant_sorcery_cast", "Instant/sorcery cast trigger",
     p(r"whenever .{0,20}cast (?:an? )?(?:instant|sorcery|noncreature) spell")),

    ("historic_cast",        "Historic cast trigger",
     p(r"whenever .{0,20}cast (?:a )?historic spell")),

    ("aura_equipment_cast",  "Aura/equipment cast trigger",
     p(r"whenever .{0,20}cast (?:an? )?(?:aura|equipment|vehicle)")),
]

# Direct oracle_text SQL equivalents — used by stages/mechanic_tags.py so that
# cast-trigger amplifier tags can be written without depending on card_abilities
# rows from tag_abilities.  Mirrors the PATTERNS above as PostgreSQL ~* (POSIX
# case-insensitive regex) WHERE fragments against the cards table.
CAST_SQL: dict[str, str] = {
    "enchantment_cast":     "oracle_text ~* 'whenever .{0,20}cast .{0,3}enchantment'",
    "creature_cast":        "oracle_text ~* 'whenever .{0,20}cast .{0,3}creature spell'",
    "artifact_cast":        "oracle_text ~* 'whenever .{0,20}cast .{0,3}artifact spell'",
    "instant_sorcery_cast": "oracle_text ~* 'whenever .{0,20}cast .{0,3}(instant|sorcery|noncreature) spell'",
    "historic_cast":        "oracle_text ~* 'whenever .{0,20}cast .{0,3}historic spell'",
    "aura_equipment_cast":  "oracle_text ~* 'whenever .{0,20}cast .{0,3}(aura|equipment|vehicle)'",
}
