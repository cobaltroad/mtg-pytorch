"""Synergy pattern registry for the MTG ingest pipeline.

Sub-modules, each covering one broad theme:

* :mod:`events`      — core event triggers (ETB, dies, attacks, cast, phase,
                        landfall, discard, token, counter, combat_damage,
                        sacrifice) and their producer SQL fragments.
* :mod:`lifegain`    — four lifegain consumer patterns (``lifegain``,
                        ``lifegain_threshold``, ``lifegain_replacement``,
                        ``lifegain_total``) and their producer SQL fragments.
* :mod:`deckbuilding` — cross-archetype deckbuilding themes (equipment,
                        legendary, graveyard, +1/+1 counters, artifacts,
                        modified, aura, proliferate, skullclamp,
                        play_from_exile).
* :mod:`tribal`      — dynamically generated tribal patterns for all tribes
                        in :data:`TRIBES`, including Zombie/Angel cross-synergy
                        overrides.
* :mod:`utility`     — utility-role patterns present in most Commander decks:
                        draw engines (``spell_draw``, ``creature_draw``,
                        ``wheel``), removal (``targeted_removal``, ``burn``,
                        ``wither``, ``bounce``, ``sweeper``), tutors
                        (``tutor_creature``, ``tutor_artifact``, ``tutor_any``),
                        interaction (``counterspell_hard``,
                        ``counterspell_conditional``, ``counterspell_redirect``,
                        ``protection``), and combat tricks (``evasion_grant``,
                        ``combat_tricks``).
* :mod:`roles`       — functional deck-role patterns (``ramp``, ``draw_one``,
                        ``draw_engine``, ``removal``, ``sweeper``, ``tutor``,
                        ``protection``, ``win_condition``, ``anthem``,
                        ``token_generator``, ``recursion``, ``interaction``,
                        ``combat_trick``, ``mana_land``, ``utility_land``).
                        Stored as ``ability_type = 'role'`` rows in
                        ``card_abilities``.

``pipeline.py`` imports :data:`TRIGGER_PATTERNS`, :data:`PRODUCER_MAP`,
:data:`TRIBES`, :data:`ROLE_PATTERNS`, and :data:`LAND_ROLE_PATTERNS` from
this package — no other changes to the pipeline are needed when a sub-module
is extended.
"""

from __future__ import annotations

from . import deckbuilding, events, lifegain, roles, tribal, utility

# Exported surface consumed by pipeline.py
TRIBES = tribal.TRIBES

TRIGGER_PATTERNS: list[tuple[str, str, str]] = [
    *events.TRIGGER_PATTERNS,
    *lifegain.TRIGGER_PATTERNS,
    *deckbuilding.TRIGGER_PATTERNS,
    *tribal.TRIGGER_PATTERNS,
    *utility.TRIGGER_PATTERNS,
]

PRODUCER_MAP: dict[str, str] = {
    **events.PRODUCER_MAP,
    **lifegain.PRODUCER_MAP,
    **deckbuilding.PRODUCER_MAP,
    **tribal.PRODUCER_MAP,
    **utility.PRODUCER_MAP,
}

ROLE_PATTERNS: list[tuple[str, str]] = roles.ROLE_PATTERNS
LAND_ROLE_PATTERNS: list[tuple[str, str]] = roles.LAND_ROLE_PATTERNS
is_land_card = roles.is_land_card

__all__ = ["TRIGGER_PATTERNS", "PRODUCER_MAP", "TRIBES", "ROLE_PATTERNS", "LAND_ROLE_PATTERNS", "is_land_card"]
