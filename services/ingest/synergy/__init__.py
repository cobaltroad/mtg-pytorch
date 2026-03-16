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

``pipeline.py`` imports :data:`TRIGGER_PATTERNS`, :data:`PRODUCER_MAP`, and
:data:`TRIBES` from this package — no other changes to the pipeline are needed
when a sub-module is extended.
"""

from __future__ import annotations

from . import deckbuilding, events, lifegain, tribal

# Exported surface consumed by pipeline.py
TRIBES = tribal.TRIBES

TRIGGER_PATTERNS: list[tuple[str, str, str]] = [
    *events.TRIGGER_PATTERNS,
    *lifegain.TRIGGER_PATTERNS,
    *deckbuilding.TRIGGER_PATTERNS,
    *tribal.TRIGGER_PATTERNS,
]

PRODUCER_MAP: dict[str, str] = {
    **events.PRODUCER_MAP,
    **lifegain.PRODUCER_MAP,
    **deckbuilding.PRODUCER_MAP,
    **tribal.PRODUCER_MAP,
}

__all__ = ["TRIGGER_PATTERNS", "PRODUCER_MAP", "TRIBES"]
