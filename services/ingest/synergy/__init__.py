"""Synergy pattern registry for the MTG ingest pipeline.

Sub-modules, each covering one broad theme:

* :mod:`events`           — core event triggers (ETB, dies, attacks, cast, phase,
                            landfall, landfall_draw, discard, token, counter,
                            combat_damage, sacrifice, sac_outlet,
                            cast_creature_spell) and their producer SQL fragments.
* :mod:`lifegain`         — four lifegain consumer patterns (``lifegain``,
                            ``lifegain_threshold``, ``lifegain_replacement``,
                            ``lifegain_total``) and their producer SQL fragments.
* :mod:`archetypes`       — commander-agnostic archetype engines (skullclamp
                            mini-combo, graveyard reanimator/fill, artifact
                            matters, modified, aura/enchantress, play_from_exile /
                            cascade).  Written as ``score_type='card_synergy'``
                            so they flow into the dataset artifact, not the
                            commander artifact.
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
                            ``combat_trick``, ``aura_equipment``, ``etb_trigger``,
                            ``wide_payoff``, ``sac_outlet``, ``discard_trigger``,
                            ``mana_land``, ``utility_land``).
                            Stored as ``ability_type = 'role'`` rows in
                            ``card_abilities``.

* :mod:`commander_mechanics` — producer SQL for commander-specific
                            pattern keys (``goad``, ``extra_combat``, ``monarch``,
                            ``initiative``, ``forced_attack``, ``poison_infect``,
                            ``group_hug``, ``second_spell``, etc.).  Merged into
                            :data:`PRODUCER_MAP` so ``compute_textmatch_synergy`` builds
                            edges for commanders tagged by ``decompose_commanders.py``.
                            Also used by that script directly for gap analysis.

``pipeline.py`` imports :data:`TRIGGER_PATTERNS`, :data:`PRODUCER_MAP`,
:data:`CONSUMER_MAP`, :data:`TRIBES`, :data:`ROLE_PATTERNS`,
:data:`LAND_ROLE_PATTERNS`, and :data:`CARD_SYNERGY_MAP` from this package —
no other changes to the pipeline are needed when a sub-module is extended.
Commander-specific maps (``commander_value``) are imported directly by
``stages/commander.py``.
"""

from __future__ import annotations

from . import commander_mechanics

PRODUCER_MAP: dict[str, str] = {
    **commander_mechanics.PATTERN_KEY_TO_PRODUCER_SQL,
}

CONSUMER_MAP: dict[str, str] = {
    **commander_mechanics.PATTERN_KEY_TO_CONSUMER_SQL,
}

__all__ = [
    "PRODUCER_MAP",
    "CONSUMER_MAP",
]
