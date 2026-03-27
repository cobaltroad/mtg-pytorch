"""XMage-class producer SQL maps for compute_xmage_synergy.

``XMAGE_PRODUCER_MAP``
    Maps XMage ability class name → SQL WHERE fragment that selects the
    *producer* cards for that ability class.  The producer set is the
    collection of cards whose presence in a deck enables/triggers the
    consumer ability.  For example, ``EntersBattlefieldTriggeredAbility``
    (Beast Whisperer) is triggered by any creature entering — so the
    producer SQL selects all creatures.

    Only classes that appear in ``xmage_parse.ABILITY_CLASS_TO_EVENT``
    need entries here; unmapped classes are silently skipped by
    ``compute_xmage_synergy``.

``SPELLCAST_TRIGGER_PRODUCER_MAP``
    Maps the refined ``trigger_event`` values assigned to
    ``SpellCastControllerTriggeredAbility`` by the body-scan in
    ``xmage_parse.parse_java_file`` → SQL WHERE fragment selecting the
    matching spell-type producers.  This prevents cross-contamination
    between Sythis (enchantment cast), Beast Whisperer (creature cast),
    and Guttersnipe (instant/sorcery cast) in Phase 2 NT-Xent training.
"""

from __future__ import annotations

from synergy.staples.mana_rocks import SQL as _MANA_ROCK_SQL

# ── SQL fragments reused across multiple ability classes ───────────────────────

_ANY_PERMANENT = (
    "type_line NOT ILIKE '%Instant%' "
    "AND type_line NOT ILIKE '%Sorcery%'"
)
_ANY_CREATURE   = "type_line ILIKE '%Creature%'"
_ANY_LAND       = "type_line ILIKE '%Land%'"
_ANY_SPELL      = "type_line NOT ILIKE '%Land%'"
_ANY_ARTIFACT   = "type_line ILIKE '%Artifact%' AND type_line NOT ILIKE '%Land%'"
_MANA_ROCK      = _MANA_ROCK_SQL
_LIFE_GAIN_PRODUCER = (
    "oracle_text ~* '(you gain|gains? [0-9]+ life|life equal|lifelink)'"
)
_DRAW_PRODUCER = (
    "oracle_text ILIKE '%draw%' AND oracle_text ILIKE '%card%'"
)
_DISCARD_PRODUCER = (
    "oracle_text ILIKE '%discard%'"
)
_COUNTER_PRODUCER = (
    "oracle_text ~* 'put[^.]*\\+1/\\+1 counter'"
)


# ── XMAGE_PRODUCER_MAP ────────────────────────────────────────────────────────
#
# Key   = XMage ability class name (matches ability_name in card_abilities)
# Value = SQL WHERE fragment for the producer card set

XMAGE_PRODUCER_MAP: dict[str, str] = {

    # ── ETB triggers ──────────────────────────────────────────────────────────
    # Triggered by any permanent entering the battlefield.
    "EntersBattlefieldTriggeredAbility":               _ANY_PERMANENT,
    "EntersBattlefieldControlledTriggeredAbility":     _ANY_PERMANENT,
    "EntersBattlefieldAllTriggeredAbility":            _ANY_PERMANENT,
    "EntersBattlefieldThisOrAnotherTriggeredAbility":  _ANY_PERMANENT,
    "EntersBattlefieldOrAttacksSourceTriggeredAbility": _ANY_PERMANENT,
    "EntersBattlefieldOrDiesSourceTriggeredAbility":   _ANY_PERMANENT,
    "EntersBattlefieldOrLeavesSourceTriggeredAbility": _ANY_PERMANENT,
    "AllyEntersBattlefieldTriggeredAbility":           _ANY_CREATURE,

    # ── Landfall ──────────────────────────────────────────────────────────────
    "LandfallAbility":                                 _ANY_LAND,

    # ── Death triggers ────────────────────────────────────────────────────────
    # Triggered by any creature dying.
    "DiesSourceTriggeredAbility":                      _ANY_CREATURE,
    "DiesCreatureTriggeredAbility":                    _ANY_CREATURE,
    "DiesAttachedTriggeredAbility":                    _ANY_CREATURE,
    "DiesThisOrAnotherTriggeredAbility":               _ANY_CREATURE,
    "PutIntoGraveFromBattlefieldSourceTriggeredAbility": _ANY_CREATURE,
    "PutIntoGraveFromBattlefieldAllTriggeredAbility":  _ANY_CREATURE,
    "DealtDamageAndDiedTriggeredAbility":              _ANY_CREATURE,
    "UndyingAbility":                                  _ANY_CREATURE,
    "PersistAbility":                                  _ANY_CREATURE,
    "AfterlifeAbility":                                _ANY_CREATURE,

    # ── Combat / attacks ──────────────────────────────────────────────────────
    "AttacksTriggeredAbility":                         _ANY_CREATURE,
    "AttacksWithCreaturesTriggeredAbility":            _ANY_CREATURE,
    "AttacksAllTriggeredAbility":                      _ANY_CREATURE,
    "AttacksOrBlocksTriggeredAbility":                 _ANY_CREATURE,
    "AttacksCreatureYouControlTriggeredAbility":       _ANY_CREATURE,
    "AttacksAttachedTriggeredAbility":                 _ANY_CREATURE,
    "AttacksAloneControlledTriggeredAbility":          _ANY_CREATURE,
    "AttacksAndIsNotBlockedTriggeredAbility":          _ANY_CREATURE,
    "AttacksWhileSaddledTriggeredAbility":             _ANY_CREATURE,

    # ── Combat damage ─────────────────────────────────────────────────────────
    "DealsCombatDamageToAPlayerTriggeredAbility":      _ANY_CREATURE,
    "DealsDamageToAPlayerAllTriggeredAbility":         _ANY_CREATURE,
    "OneOrMoreCombatDamagePlayerTriggeredAbility":     _ANY_CREATURE,
    "DealsDamageToOpponentTriggeredAbility":           _ANY_CREATURE,
    "DealsDamageToAPlayerAttachedTriggeredAbility":    _ANY_CREATURE,
    "DealsDamageSourceTriggeredAbility":               _ANY_CREATURE,
    "DealsDamageToAPlayerTriggeredAbility":            _ANY_CREATURE,

    # ── Spellcasting ──────────────────────────────────────────────────────────
    # SpellCastControllerTriggeredAbility is sub-bucketed by trigger_event in
    # compute_xmage_synergy; the entry here is the generic fallback only used
    # when no refined trigger_event is present.
    "SpellCastControllerTriggeredAbility":             _ANY_SPELL,
    "SpellCastOpponentTriggeredAbility":               _ANY_SPELL,
    "SpellCastAllTriggeredAbility":                    _ANY_SPELL,
    "MagecraftAbility":                                _ANY_SPELL,
    "CastSecondSpellTriggeredAbility":                 _ANY_SPELL,
    "HeroicAbility":                                   _ANY_SPELL,

    # ── Sacrifice ─────────────────────────────────────────────────────────────
    "SacrificePermanentTriggeredAbility":              _ANY_PERMANENT,
    "ExploitCreatureTriggeredAbility":                 _ANY_CREATURE,

    # ── Draw ──────────────────────────────────────────────────────────────────
    "DrawCardControllerTriggeredAbility":              _DRAW_PRODUCER,
    "DrawNthCardTriggeredAbility":                     _DRAW_PRODUCER,

    # ── Lifegain ──────────────────────────────────────────────────────────────
    "GainLifeControllerTriggeredAbility":              _LIFE_GAIN_PRODUCER,

    # ── Discard / cycling ─────────────────────────────────────────────────────
    "CycleTriggeredAbility":                           _DISCARD_PRODUCER,
    "CycleOrDiscardControllerTriggeredAbility":        _DISCARD_PRODUCER,

    # ── Counter placement ─────────────────────────────────────────────────────
    "OneOrMoreCountersAddedTriggeredAbility":          _COUNTER_PRODUCER,

    # ── Counter growth keywords ────────────────────────────────────────────────
    "EvolveAbility":                                   _ANY_CREATURE,
    "AdaptAbility":                                    _ANY_CREATURE,
    "GraftAbility":                                    _ANY_CREATURE,
    "ModularAbility":                                  _ANY_ARTIFACT,
    "RiotAbility":                                     _ANY_CREATURE,

    # ── Mana rocks ────────────────────────────────────────────────────────────
    # Peer edges: every mana rock is a producer for every other mana rock.
    "SimpleManaAbility":                               _MANA_ROCK,
    "ColorlessManaAbility":                            _MANA_ROCK,
    "CommanderColorIdentityManaAbility":               _MANA_ROCK,
    "AnyColorManaAbility":                             _MANA_ROCK,
    "AnyColorLandsProduceManaAbility":                 _MANA_ROCK,
    "BlackManaAbility":                                _MANA_ROCK,
    "BlueManaAbility":                                 _MANA_ROCK,
    "RedManaAbility":                                  _MANA_ROCK,
    "WhiteManaAbility":                                _MANA_ROCK,
    "GreenManaAbility":                                _MANA_ROCK,
}


# ── SPELLCAST_TRIGGER_PRODUCER_MAP ────────────────────────────────────────────
#
# Used by compute_xmage_synergy when processing SpellCastControllerTriggeredAbility
# consumers.  Each entry selects only the spell type that actually triggers
# the consumer's ability, preventing cross-bucket false positives.
#
# Key   = refined trigger_event from xmage_parse.SPELLCAST_FILTER_MAP
# Value = SQL WHERE fragment for matching spell producers

SPELLCAST_TRIGGER_PRODUCER_MAP: dict[str, str] = {
    "enchantment_cast":    "type_line ILIKE '%Enchantment%' AND type_line NOT ILIKE '%Land%'",
    "artifact_cast":       "type_line ILIKE '%Artifact%'    AND type_line NOT ILIKE '%Land%'",
    "creature_cast":       "type_line ILIKE '%Creature%'",
    "instant_sorcery_cast": (
        "type_line ILIKE '%Instant%' OR type_line ILIKE '%Sorcery%'"
    ),
    "noncreature_cast":    (
        "type_line NOT ILIKE '%Creature%' AND type_line NOT ILIKE '%Land%'"
    ),
    "historic_cast":       (
        "type_line ILIKE '%Artifact%' "
        "OR (type_line ILIKE '%Legendary%' AND type_line NOT ILIKE '%Land%') "
        "OR type_line ILIKE '%Saga%'"
    ),
    "spirit_arcane_cast":  (
        "type_line ILIKE '%Spirit%' "
        "OR (type_line ILIKE '%Instant%' AND type_line ILIKE '%Arcane%') "
        "OR (type_line ILIKE '%Sorcery%' AND type_line ILIKE '%Arcane%')"
    ),
    # Generic fallback — any non-land card
    "spell_cast":          _ANY_SPELL,
}
