"""Synergy pattern registry for the MTG ingest pipeline.

Sub-modules, each covering one broad theme:

* :mod:`events`           — core event triggers (ETB, dies, attacks, cast, phase,
                            landfall, landfall_draw, discard, token, counter,
                            combat_damage, sacrifice, sac_outlet,
                            cast_creature_spell) and their producer SQL fragments.
* :mod:`lifegain`         — four lifegain consumer patterns (``lifegain``,
                            ``lifegain_threshold``, ``lifegain_replacement``,
                            ``lifegain_total``) and their producer SQL fragments.
* :mod:`deckbuilding`     — cross-archetype deckbuilding themes (equipment,
                            legendary, graveyard, +1/+1 counters, artifacts,
                            modified, aura, proliferate, skullclamp,
                            play_from_exile, enchantress, adapt_evolve).
* :mod:`tribal`           — dynamically generated tribal patterns for all tribes
                            in :data:`TRIBES`, including Zombie/Angel cross-synergy
                            overrides.
* :mod:`utility`          — utility-role patterns present in most Commander decks:
                            draw engines (``spell_draw``, ``creature_draw``,
                            ``wheel``), removal (``targeted_removal``, ``burn``,
                            ``wither``, ``bounce``, ``sweeper``), tutors
                            (``tutor_creature``, ``tutor_artifact``, ``tutor_any``),
                            interaction (``counterspell_hard``,
                            ``counterspell_conditional``, ``counterspell_redirect``,
                            ``protection``), and combat tricks (``evasion_grant``,
                            ``combat_tricks``).
* :mod:`commander_value`  — "free-if-commander" and persistent-bonus cards that
                            reward having a commander in play (Deflecting Swat,
                            Fierce Guardianship, Loyal Apprentice, Jeska's Will,
                            Mox Amber, …).  Producers are low-MV (CMC ≤ 2)
                            legendary creatures/planeswalkers.  These edges use
                            ``score_type = 'commander_value'`` and are built by
                            the dedicated ``compute_commander_value_synergy()``
                            stage in ``pipeline.py``.
* :mod:`roles`            — functional deck-role patterns (``ramp``, ``draw_one``,
                            ``repeatable_draw``, ``removal``, ``sweeper``, ``tutor``,
                            ``protection``, ``win_condition``, ``anthem``,
                            ``token_generator``, ``recursion``, ``interaction``,
                            ``combat_trick``, ``mana_land``, ``utility_land``).
                            Stored as ``ability_type = 'role'`` rows in
                            ``card_abilities``.

* :mod:`commander_mechanics` — producer SQL for commander-specific
                            pattern keys (``goad``, ``extra_combat``, ``monarch``,
                            ``initiative``, ``forced_attack``, ``poison_infect``,
                            ``group_hug``, ``second_spell``, etc.).  Merged into
                            :data:`PRODUCER_MAP` so ``compute_synergy`` builds
                            edges for commanders tagged by ``decompose_commanders.py``.
                            Also used by that script directly for gap analysis.

``pipeline.py`` imports :data:`TRIGGER_PATTERNS`, :data:`PRODUCER_MAP`,
:data:`TRIBES`, :data:`ROLE_PATTERNS`, :data:`LAND_ROLE_PATTERNS`, and
:data:`COMMANDER_VALUE_EDGE_SCORES` from this package — no other changes to
the pipeline are needed when a sub-module is extended.
"""

from __future__ import annotations

from . import commander_value, commander_mechanics, deckbuilding, events, lifegain, roles, tribal, utility, xmage

# Exported surface consumed by pipeline.py
TRIBES = tribal.TRIBES
ALL_TYPES_SQL = tribal.ALL_TYPES_SQL

TRIGGER_PATTERNS: list[tuple[str, str, str]] = [
    *events.TRIGGER_PATTERNS,
    *lifegain.TRIGGER_PATTERNS,
    *deckbuilding.TRIGGER_PATTERNS,
    *tribal.TRIGGER_PATTERNS,
    *utility.TRIGGER_PATTERNS,
    *commander_value.TRIGGER_PATTERNS,
]

PRODUCER_MAP: dict[str, str] = {
    # Commander-specific mechanics (goad, extra_combat, monarch, etc.) — SQL
    # lives in commander_mechanics.py, which is also used by
    # decompose_commanders.py for gap analysis.  Merged first so that the
    # existing sub-modules below can override any key they handle more precisely.
    **commander_mechanics.PATTERN_KEY_TO_PRODUCER_SQL,
    # Core synergy sub-modules (take precedence over commander mechanics for
    # any pattern keys that overlap, e.g. equipment_matters, proliferate_matters).
    **events.PRODUCER_MAP,
    **lifegain.PRODUCER_MAP,
    **deckbuilding.PRODUCER_MAP,
    **tribal.PRODUCER_MAP,
    **utility.PRODUCER_MAP,
    # commander_value producers are NOT merged into PRODUCER_MAP — they use a
    # dedicated pipeline stage (compute_commander_value_synergy) that writes
    # score_type='commander_value' edges with per-event scores, rather than
    # the flat score=1.0 / score_type='ability_trigger' that compute_synergy()
    # uses.  The TRIGGER_PATTERNS above still tag consumer cards in
    # card_abilities so the dedicated stage can cross-join against them.
}

ROLE_PATTERNS: list[tuple[str, str]] = roles.ROLE_PATTERNS
LAND_ROLE_PATTERNS: list[tuple[str, str]] = roles.LAND_ROLE_PATTERNS
is_land_card = roles.is_land_card

# Per-trigger-event scores for commander_value edges (used by the dedicated
# compute_commander_value_synergy() pipeline stage).
COMMANDER_VALUE_TRIGGER_PATTERNS: list[tuple[str, str, str]] = commander_value.TRIGGER_PATTERNS
COMMANDER_VALUE_PRODUCER_MAP: dict[str, str] = commander_value.PRODUCER_MAP
COMMANDER_VALUE_EDGE_SCORES: dict[str, float] = commander_value.EDGE_SCORES

# Compositional training path: XMage class name → producer SQL.
# Used by compute_synergy_xmage() to build score_type='xmage_ability_trigger'
# edges without the lossy trigger_event translation layer.
XMAGE_PRODUCER_MAP: dict[str, str] = xmage.XMAGE_PRODUCER_MAP

# SpellCastControllerTriggeredAbility sub-bucket → producer SQL.
# Used by compute_synergy_xmage() to select type-specific producers for each
# refined trigger_event (e.g. "enchantment_cast" → enchantment producers only).
SPELLCAST_TRIGGER_PRODUCER_MAP: dict[str, str] = xmage.SPELLCAST_TRIGGER_PRODUCER_MAP

__all__ = [
    "TRIGGER_PATTERNS",
    "PRODUCER_MAP",
    "TRIBES",
    "ALL_TYPES_SQL",
    "ROLE_PATTERNS",
    "LAND_ROLE_PATTERNS",
    "is_land_card",
    "COMMANDER_VALUE_TRIGGER_PATTERNS",
    "COMMANDER_VALUE_PRODUCER_MAP",
    "COMMANDER_VALUE_EDGE_SCORES",
    "XMAGE_PRODUCER_MAP",
    "SPELLCAST_TRIGGER_PRODUCER_MAP",
]
