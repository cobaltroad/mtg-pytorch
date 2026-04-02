"""Synergy pattern registry for the MTG ingest pipeline.

Sub-packages and modules
------------------------

* :mod:`triggered_ability` — oracle-text patterns for triggered abilities,
                             aggregated as ``TRIGGERED_ABILITY_PATTERNS``.
                             Sub-modules: ``attack``, ``counter``, ``lifegain``,
                             ``draw``, ``creature_etb``, ``sacrifice``.

* :mod:`activated_ability` — oracle-text patterns for activated abilities,
                             aggregated as ``ACTIVATED_ABILITY_PATTERNS``.
                             Sub-modules: ``mana_producer``, ``sac_outlet``.

* :mod:`combat`            — oracle-text patterns for combat-enabler cards,
                             aggregated as ``COMBAT_PATTERNS``.
                             Sub-module: ``combat``.

* :mod:`tribal`            — dynamically generated tribal patterns for all
                             supported tribes in :data:`TRIBES`.
                             Exports ``TRIBAL_PATTERNS``, ``TRIBES``,
                             ``tribal_sql``, ``oracle_mention_sql``.

* :mod:`staples`           — color-identity-gated Commander staple categories
                             (mana rocks, land ramp, mana dorks, removal,
                             sweeper, draw, interaction, lands).  Exports
                             :data:`STAPLE_CATEGORIES` used by
                             ``export_dataset_commanders.py``.

* :mod:`commander_mechanics` — producer and consumer SQL fragments keyed by
                             mechanic pattern key (e.g. ``counter_placement``,
                             ``attack_trigger``, ``tribal_elf``).  Exports
                             :data:`PATTERN_KEY_TO_PRODUCER_SQL` and
                             :data:`PATTERN_KEY_TO_CONSUMER_SQL`.  Used by
                             ``stages/decompose.py`` for gap analysis and by
                             ``export_dataset_commanders.py`` to build
                             per-commander positive sets.

* :mod:`xmage`             — XMage-class producer SQL maps used by
                             ``compute_xmage_synergy``.  Exports
                             :data:`XMAGE_PRODUCER_MAP` and
                             :data:`SPELLCAST_TRIGGER_PRODUCER_MAP`.

* :mod:`mechanic_keys`     — ``MechanicKey`` StrEnum: single source of truth
                             for all mechanic/synergy key strings.

Package-level exports
---------------------

:data:`PRODUCER_MAP`
    Merged dict of ``PATTERN_KEY_TO_PRODUCER_SQL`` (from
    ``commander_mechanics``) plus ``"mana_rock"`` SQL.  Consumed by
    ``stages/dataset.py::compute_textmatch_synergy`` to build
    ``ability_trigger`` synergy edges.

:data:`CONSUMER_MAP`
    Alias for ``PATTERN_KEY_TO_CONSUMER_SQL`` from ``commander_mechanics``.

:data:`XMAGE_PRODUCER_MAP`, :data:`SPELLCAST_TRIGGER_PRODUCER_MAP`
    Re-exported from ``xmage`` for use by ``compute_xmage_synergy``.

Typical import pattern by stage
--------------------------------
``stages/tag.py``
    imports ``TRIGGERED_ABILITY_PATTERNS``, ``ACTIVATED_ABILITY_PATTERNS``,
    ``COMBAT_PATTERNS`` from the sub-packages, and ``TRIBAL_PATTERNS`` from
    ``synergy.tribal``.

``stages/dataset.py``
    imports ``PRODUCER_MAP``, ``XMAGE_PRODUCER_MAP``,
    ``SPELLCAST_TRIGGER_PRODUCER_MAP`` from this package.

``stages/decompose.py``
    imports ``PATTERN_KEY_TO_PRODUCER_SQL``, ``PATTERN_KEY_TO_CONSUMER_SQL``
    from ``synergy.commander_mechanics``, and ``TRIBES`` from
    ``synergy.tribal``.

``export_dataset_commanders.py``
    imports ``PATTERN_KEY_TO_PRODUCER_SQL``, ``PATTERN_KEY_TO_CONSUMER_SQL``
    from ``synergy.commander_mechanics``, and ``STAPLE_CATEGORIES`` from
    ``synergy.staples``.
"""

from __future__ import annotations

from . import commander_mechanics
from mtg_sql.staples.mana_rocks import SQL as _MANA_ROCK_SQL
from .xmage import XMAGE_PRODUCER_MAP, SPELLCAST_TRIGGER_PRODUCER_MAP  # noqa: F401

PRODUCER_MAP: dict[str, str] = {
    **commander_mechanics.PATTERN_KEY_TO_PRODUCER_SQL,
    # Mana rocks — peer edges between all artifact mana producers.
    # Both producer and consumer are the mana_rock set; this creates
    # every-mana-rock → every-other-mana-rock ability_trigger edges so
    # Phase 2 NT-Xent sees them as positive pairs rather than orphans.
    "mana_rock": _MANA_ROCK_SQL,
}

CONSUMER_MAP: dict[str, str] = {
    **commander_mechanics.PATTERN_KEY_TO_CONSUMER_SQL,
}

__all__ = [
    "PRODUCER_MAP",
    "CONSUMER_MAP",
    "XMAGE_PRODUCER_MAP",
    "SPELLCAST_TRIGGER_PRODUCER_MAP",
]
