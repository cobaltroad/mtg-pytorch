"""XMage ability-class → producer SQL mapping for the compositional training path.

Each key is an XMage ability class name (the ``ability_name`` column in
``card_abilities`` where ``source='xmage'``).  The value is a SQL WHERE
fragment that selects *producer* cards from the ``cards`` table — i.e. cards
whose presence on the battlefield (or in a deck) enables the keyed ability to
trigger.

Used by ``compute_synergy_xmage()`` in ``pipeline.py`` to build
``synergy_edges`` rows with ``score_type='xmage_ability_trigger'``.

The co-occurrence training path uses ``PRODUCER_MAP`` (keyed by aggregated
``trigger_event`` strings such as ``creature_etb`` or ``dies``) instead.  The
compositional path bypasses that translation entirely — XMage class names are
the ground-truth event identifiers.

Because XMage uses ``SpellCastControllerTriggeredAbility`` for *all*
spell-cast triggers (regardless of spell type), Beast Whisperer and Sythis
land in the same consumer bucket.  This is intentional: they ARE functionally
similar (both reward spell-casting), and Phase 2 of the compositional path
will learn that cluster.  The producer SQL is broadened to all non-land spell
types to reflect this.
"""

from __future__ import annotations

# ── Shared producer SQL fragments ─────────────────────────────────────────────

_CREATURE = "lower(type_line) LIKE '%creature%'"

_INSTANT_SORCERY = (
    "lower(type_line) LIKE '%instant%' OR lower(type_line) LIKE '%sorcery%'"
)

# Any castable non-land card — used for generic spell-cast triggers whose
# filter is not inspectable from the import list alone.
_ANY_SPELL = (
    "lower(type_line) LIKE '%instant%'"
    " OR lower(type_line) LIKE '%sorcery%'"
    " OR lower(type_line) LIKE '%creature%'"
    " OR lower(type_line) LIKE '%enchantment%'"
    " OR lower(type_line) LIKE '%artifact%'"
    " OR lower(type_line) LIKE '%planeswalker%'"
)

_LANDFALL = (
    "lower(type_line) LIKE '%land%'"
    " OR (lower(oracle_text) LIKE '%search your library%'"
    "     AND lower(oracle_text) LIKE '%land%'"
    "     AND lower(oracle_text) LIKE '%put it onto the battlefield%')"
)

_DRAW = (
    "lower(oracle_text) LIKE '%draw%' AND lower(oracle_text) LIKE '%card%'"
)

_LIFEGAIN = (
    "lower(oracle_text) LIKE '%gain%' AND lower(oracle_text) LIKE '%life%'"
)

_CYCLING = (
    "lower(oracle_text) LIKE '%cycling%' OR lower(oracle_text) LIKE '%discard%'"
)

_COUNTER = (
    "lower(oracle_text) LIKE '%put a%counter%'"
    " OR lower(oracle_text) LIKE '%proliferate%'"
)

_ARTIFACT_CREATURE = (
    "lower(type_line) LIKE '%artifact%' AND lower(type_line) LIKE '%creature%'"
)

_ENCHANTMENT = "lower(type_line) LIKE '%enchantment%'"

_ARTIFACT = "lower(type_line) LIKE '%artifact%'"

_NON_CREATURE = (
    "lower(type_line) NOT LIKE '%creature%'"
    " AND lower(type_line) NOT LIKE '%land%'"
)

_HISTORIC = (
    "lower(type_line) LIKE '%artifact%'"
    " OR lower(type_line) LIKE '%legendary%'"
    " OR lower(oracle_text) LIKE '%saga%'"
)

_SPIRIT_ARCANE = (
    "lower(type_line) LIKE '%spirit%'"
    " OR lower(oracle_text) LIKE '% arcane%'"
    " OR lower(type_line) LIKE '%arcane%'"
)


# ── SpellCastControllerTriggeredAbility trigger_event → producer SQL ──────────
#
# Used by compute_synergy_xmage() to sub-group SpellCastControllerTriggeredAbility
# consumers by their refined trigger_event (set by the body-scan in xmage_parse.py)
# and select the appropriate producer cards for each sub-bucket.
#
# Keys match the values in xmage_parse.SPELLCAST_FILTER_MAP plus "spell_cast"
# as the fallback for cards whose constructor call has no StaticFilters argument.

SPELLCAST_TRIGGER_PRODUCER_MAP: dict[str, str] = {
    "spirit_arcane_cast":    _SPIRIT_ARCANE,
    "enchantment_cast":      _ENCHANTMENT,
    "artifact_cast":         _ARTIFACT,
    "noncreature_cast":      _NON_CREATURE,
    "creature_cast":         _CREATURE,
    "instant_sorcery_cast":  _INSTANT_SORCERY,
    "historic_cast":         _HISTORIC,
    "spell_cast":            _ANY_SPELL,
}


# ── XMage class → producer SQL ────────────────────────────────────────────────

XMAGE_PRODUCER_MAP: dict[str, str] = {
    # ── ETB triggers ──────────────────────────────────────────────────────────
    "EntersBattlefieldTriggeredAbility":               _CREATURE,
    "EntersBattlefieldControlledTriggeredAbility":     _CREATURE,
    "EntersBattlefieldAllTriggeredAbility":            _CREATURE,
    "EntersBattlefieldThisOrAnotherTriggeredAbility":  _CREATURE,
    "EntersBattlefieldOrAttacksSourceTriggeredAbility": _CREATURE,
    "EntersBattlefieldOrDiesSourceTriggeredAbility":   _CREATURE,
    "EntersBattlefieldOrLeavesSourceTriggeredAbility": _CREATURE,
    "AllyEntersBattlefieldTriggeredAbility":           _CREATURE,

    # ── Landfall ──────────────────────────────────────────────────────────────
    "LandfallAbility": _LANDFALL,

    # ── Death triggers ────────────────────────────────────────────────────────
    "DiesSourceTriggeredAbility":                         _CREATURE,
    "DiesCreatureTriggeredAbility":                       _CREATURE,
    "DiesAttachedTriggeredAbility":                       _CREATURE,
    "DiesThisOrAnotherTriggeredAbility":                  _CREATURE,
    "PutIntoGraveFromBattlefieldSourceTriggeredAbility":  _CREATURE,
    "PutIntoGraveFromBattlefieldAllTriggeredAbility":     _CREATURE,
    "DealtDamageAndDiedTriggeredAbility":                 _CREATURE,
    "UndyingAbility":                                     _CREATURE,
    "PersistAbility":                                     _CREATURE,
    "AfterlifeAbility":                                   _CREATURE,

    # ── Combat / attacks ──────────────────────────────────────────────────────
    "AttacksTriggeredAbility":                        _CREATURE,
    "AttacksWithCreaturesTriggeredAbility":           _CREATURE,
    "AttacksAllTriggeredAbility":                     _CREATURE,
    "AttacksOrBlocksTriggeredAbility":                _CREATURE,
    "AttacksCreatureYouControlTriggeredAbility":      _CREATURE,
    "AttacksAttachedTriggeredAbility":                _CREATURE,
    "AttacksAloneControlledTriggeredAbility":         _CREATURE,
    "AttacksAndIsNotBlockedTriggeredAbility":         _CREATURE,
    "AttacksWhileSaddledTriggeredAbility":            _CREATURE,

    # ── Combat damage ─────────────────────────────────────────────────────────
    "DealsCombatDamageToAPlayerTriggeredAbility":     _CREATURE,
    "DealsDamageToAPlayerAllTriggeredAbility":        _CREATURE,
    "OneOrMoreCombatDamagePlayerTriggeredAbility":    _CREATURE,
    "DealsDamageToOpponentTriggeredAbility":          _CREATURE,
    "DealsDamageToAPlayerAttachedTriggeredAbility":   _CREATURE,
    "DealsDamageSourceTriggeredAbility":              _CREATURE,
    "DealsDamageToAPlayerTriggeredAbility":           _CREATURE,

    # ── Spellcasting ──────────────────────────────────────────────────────────
    # SpellCastControllerTriggeredAbility is sub-grouped by trigger_event in
    # compute_synergy_xmage() using SPELLCAST_TRIGGER_PRODUCER_MAP, so
    # XMAGE_PRODUCER_MAP entry here acts as the fallback for cards where no
    # StaticFilters argument was detected (generic "whenever you cast a spell").
    "SpellCastControllerTriggeredAbility":  _ANY_SPELL,
    "SpellCastOpponentTriggeredAbility":    _ANY_SPELL,
    "SpellCastAllTriggeredAbility":         _ANY_SPELL,
    # Magecraft and heroic are specifically instant/sorcery interactions.
    "MagecraftAbility":            _INSTANT_SORCERY,
    "CastSecondSpellTriggeredAbility": _INSTANT_SORCERY,
    "HeroicAbility":               _INSTANT_SORCERY,

    # ── Sacrifice ─────────────────────────────────────────────────────────────
    "SacrificePermanentTriggeredAbility": _CREATURE,
    "ExploitCreatureTriggeredAbility":    _CREATURE,

    # ── Draw ──────────────────────────────────────────────────────────────────
    "DrawCardControllerTriggeredAbility": _DRAW,
    "DrawNthCardTriggeredAbility":        _DRAW,

    # ── Lifegain ──────────────────────────────────────────────────────────────
    "GainLifeControllerTriggeredAbility": _LIFEGAIN,

    # ── Discard / cycling ─────────────────────────────────────────────────────
    "CycleTriggeredAbility":                    _CYCLING,
    "CycleOrDiscardControllerTriggeredAbility": _CYCLING,

    # ── Counter placement ─────────────────────────────────────────────────────
    "OneOrMoreCountersAddedTriggeredAbility": _COUNTER,

    # ── Counter growth keywords (adapt_evolve) ────────────────────────────────
    "EvolveAbility":  _CREATURE,       # needs a bigger creature entering
    "AdaptAbility":   _COUNTER,        # synergises with proliferate / counter adders
    "GraftAbility":   _COUNTER,
    "ModularAbility": _ARTIFACT_CREATURE,
    "RiotAbility":    _CREATURE,
}
