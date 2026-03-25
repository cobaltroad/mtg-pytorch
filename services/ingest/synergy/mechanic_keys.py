"""Authoritative enumeration of all mechanic / synergy keys used across the
commander pipeline.

This is the **single source of truth** for key strings.  Import ``MechanicKey``
anywhere a key string is needed so that typos are caught at import time and
renaming a key is a one-file change.

Key naming conventions
----------------------
* Oracle-text keys (emitted by ``decompose_commanders.py``) use the form
  ``<phenomenon>_<role>``, e.g. ``etb_trigger``, ``death_trigger``.
* XMage-derived aliases shadow oracle keys with short, game-engine names, e.g.
  ``creature_etb``, ``dies``.  They share values deliberately to allow SQL
  producer fragments in ``commander_mechanics.py`` to differ slightly while
  expressing the same mechanic family.
* Utility roles (``utility.py``) cover support cards that belong in most decks
  regardless of archetype: removal, tutors, interaction, combat tricks.
* Fine-grained event keys (``events.py``) sub-divide broad families into more
  precise buckets used by the card-tagging stage.

Sections (in order)
-------------------
1.  ETB / enters-the-battlefield
2.  Death / dies / graveyard
3.  Attack / combat
4.  Spell-cast families
5.  Sacrifice
6.  Discard / cycling
7.  Landfall
8.  Counters
9.  Lifegain
10. Draw / card advantage
11. Tokens
12. Trigger interaction / phase
13. Static / keyword effects
14. Equipment & artifacts
15. Forced combat / political (goad, monarch, initiative)
16. Stax / restriction
17. Utility roles — removal, tutors, interaction, combat tricks
"""

from __future__ import annotations

from enum import StrEnum


class MechanicKey(StrEnum):
    """All mechanic / synergy key strings, grouped by game concept.

    Being a ``StrEnum`` every member compares equal to its string value::

        MechanicKey.ETB_TRIGGER == "etb_trigger"   # True
        "etb_trigger" in {MechanicKey.ETB_TRIGGER}  # True

    Iterate over all keys: ``list(MechanicKey)``
    """

    # =========================================================================
    # 1. ETB / enters-the-battlefield
    # =========================================================================
    # Primary oracle-text key (decompose_commanders.py ORACLE_PATTERNS)
    ETB_TRIGGER            = "etb_trigger"
    # XMage alias — more specific (creature enters the battlefield)
    CREATURE_ETB           = "creature_etb"
    # Generic catch-all (any permanent entering; commander_mechanics.py alias)
    ENTERS_BATTLEFIELD     = "enters_battlefield"
    # events.py fine-grained: only non-token creatures
    NONTOKEN_ETB           = "nontoken_etb"
    # events.py fine-grained: artifact entering
    ARTIFACT_ETB           = "artifact_etb"

    # =========================================================================
    # 2. Death / dies / graveyard
    # =========================================================================
    DEATH_TRIGGER          = "death_trigger"
    # XMage alias
    DIES                   = "dies"
    # events.py fine-grained: non-token only (common Aristocrats template)
    NONTOKEN_DIES          = "nontoken_dies"
    # Permanent put into graveyard from the battlefield (broader than creature)
    GRAVEYARD_FROM_PLAY    = "graveyard_from_play"
    # Commander casts from / returns things from the graveyard
    GRAVEYARD_PAYOFF       = "graveyard_payoff"
    # Unearth / encore / Feldon-style temporary-reanimation commanders
    UNEARTH_ENCORE         = "unearth_encore"

    # =========================================================================
    # 3. Attack / combat
    # =========================================================================
    ATTACK_TRIGGER         = "attack_trigger"
    # XMage alias
    ATTACKS                = "attacks"
    COMBAT_DAMAGE_TO_PLAYER = "combat_damage_to_player"
    # XMage alias
    COMBAT_DAMAGE          = "combat_damage"
    # Commander grants / rewards additional combat phases
    EXTRA_COMBAT           = "extra_combat"

    # =========================================================================
    # 4. Spell-cast families
    # =========================================================================
    # ── Oracle / decompose keys ───────────────────────────────────────────────
    CAST_TRIGGER_CREATURE        = "cast_trigger_creature"
    CAST_TRIGGER_INSTANT_SORCERY = "cast_trigger_instant_sorcery"
    CAST_TRIGGER_ENCHANTMENT     = "cast_trigger_enchantment"
    CAST_TRIGGER_ARTIFACT        = "cast_trigger_artifact"
    CAST_TRIGGER_HISTORIC        = "cast_trigger_historic"
    # Color-word cast trigger ("whenever you cast a red spell")
    CAST_TRIGGER_COLORED         = "cast_trigger_colored"

    # ── XMage / SPELLCAST_FILTER_MAP refined keys ─────────────────────────────
    # Exact equivalents of the oracle keys above; kept separate so
    # commander_mechanics.py can assign different SQL if needed.
    CREATURE_CAST          = "creature_cast"
    INSTANT_SORCERY_CAST   = "instant_sorcery_cast"
    ENCHANTMENT_CAST       = "enchantment_cast"
    ARTIFACT_CAST          = "artifact_cast"
    HISTORIC_CAST          = "historic_cast"
    NONCREATURE_CAST       = "noncreature_cast"
    # Kamigawa-block Arcane spells + Spirits
    SPIRIT_ARCANE_CAST     = "spirit_arcane_cast"
    # Generic fallback when no SPELLCAST_FILTER_MAP entry is found
    SPELL_CAST             = "spell_cast"

    # ── events.py alias ───────────────────────────────────────────────────────
    # "whenever you cast a creature spell" phrasing (Beast Whisperer template)
    CAST_CREATURE_SPELL    = "cast_creature_spell"

    # Commander rewards casting a second (or later) spell per turn
    SECOND_SPELL           = "second_spell"
    # Cascade / discover keyword commanders
    CASCADE                = "cascade"

    # =========================================================================
    # 5. Sacrifice
    # =========================================================================
    SACRIFICE_PAYOFF       = "sacrifice_payoff"
    # XMage alias
    SACRIFICE              = "sacrifice"

    # =========================================================================
    # 6. Discard / cycling
    # =========================================================================
    # Commander has a discard outlet or payoff
    DISCARD_OUTLET         = "discard_outlet"
    # XMage alias
    DISCARD                = "discard"
    # Commander specifically rewards the Madness keyword
    MADNESS_PAYOFF         = "madness_payoff"
    # Commander triggers on the Cycling keyword
    CYCLING_TRIGGER        = "cycling_trigger"

    # =========================================================================
    # 7. Landfall
    # =========================================================================
    LANDFALL               = "landfall"
    # commander_mechanics.py XMage-derived variant (identical SQL, kept for gap tracking)
    LANDFALL_XMAGE         = "landfall_xmage"
    # events.py fine-grained: landfall that specifically draws a card
    LANDFALL_DRAW          = "landfall_draw"

    # =========================================================================
    # 8. Counters
    # =========================================================================
    # Commander places +1/+1 counters
    COUNTER_PLACEMENT      = "counter_placement"
    # XMage alias — "one or more counters added" trigger
    COUNTER_ADDED          = "counter_added"
    # Commander doubles counter accumulation
    COUNTER_DOUBLER        = "counter_doubler"
    # Proliferate-centric commanders (Atraxa, etc.)
    PROLIFERATE_MATTERS    = "proliferate_matters"
    # XMage keywords: evolve, adapt, graft, modular, riot
    ADAPT_EVOLVE           = "adapt_evolve"

    # =========================================================================
    # 9. Lifegain
    # =========================================================================
    LIFEGAIN_TRIGGER       = "lifegain_trigger"
    # XMage alias
    LIFEGAIN               = "lifegain"

    # =========================================================================
    # 10. Draw / card advantage
    # =========================================================================
    # Commander triggers on drawing cards (Niv-Mizzet, etc.)
    DRAW_TRIGGER           = "draw_trigger"
    # Spell-cast / player-draw advantage payoffs (Rhystic Study, Smothering Tithe)
    SPELL_DRAW             = "spell_draw"
    # Draw payoffs that fire on creature ETB, dies, or combat damage
    CREATURE_DRAW          = "creature_draw"
    # Mass draw / loot effects (Wheel of Fortune, Windfall, Jace's Archivist)
    WHEEL                  = "wheel"

    # =========================================================================
    # 11. Tokens
    # =========================================================================
    # Commander triggers on token creation
    TOKEN_TRIGGER          = "token_trigger"
    # events.py alias ("you create … token" wording)
    TOKEN_CREATION         = "token_creation"

    # =========================================================================
    # 12. Trigger interaction / phase
    # =========================================================================
    # Commander doubles / copies triggered abilities
    TRIGGER_DOUBLING       = "trigger_doubling"
    # events.py upkeep / end-step triggers
    PHASE_BEGIN            = "phase_begin"

    # =========================================================================
    # 13. Static / keyword-grant effects
    # =========================================================================
    # Commander grants a keyword to its team (Odric, Akroma, etc.)
    KEYWORD_LORD           = "keyword_lord"
    # Infect / toxic / poison-counter commanders
    POISON_INFECT          = "poison_infect"
    # Commander gives resources to all players (Kami, Kwain, Kynaios)
    GROUP_HUG              = "group_hug"
    # Commander cares about low-power creatures (Edric, etc.)
    WEENIE_MATTERS         = "weenie_matters"
    # Commander drains each opponent on trigger (Mogis, Nekusar)
    PUNISHER               = "punisher"

    # =========================================================================
    # 13b. Tribal
    # =========================================================================
    # Elf tribal — Tyvar-style commanders that demand Elf creatures
    TRIBAL_ELF             = "tribal_elf"

    # =========================================================================
    # 13c. Mana / ramp roles
    # =========================================================================
    # Creatures with a mana ability (tap for mana) — mana dorks
    MANA_DORK              = "mana_dork"
    # +1/+1 counter payoff / amplifier (Tyvar's second-ability output)
    COUNTER_TRIGGER        = "counter_trigger"

    # =========================================================================
    # 14. Equipment & artifacts
    # =========================================================================
    # Commander cares about Equipment being attached / equipped / cast
    EQUIPMENT_MATTERS      = "equipment_matters"
    # Commander scales with the *number* of artifacts you control
    ARTIFACT_COUNT         = "artifact_count"
    # Commander buffs / triggers off artifact *creatures* specifically
    ARTIFACT_CREATURES     = "artifact_creatures"

    # =========================================================================
    # 15. Forced combat / political mechanics
    # =========================================================================
    # Commander goads opponents' creatures
    GOAD                   = "goad"
    # Commander forces all / certain creatures to attack each combat
    FORCED_ATTACK          = "forced_attack"
    # Commander interacts with the Monarch mechanic
    MONARCH                = "monarch"
    # Commander interacts with the Initiative / Undercity mechanic
    INITIATIVE             = "initiative"

    # =========================================================================
    # 16. Stax / restriction
    # =========================================================================
    # "Opponents can't …" blanket restriction
    OPPONENT_RESTRICTION   = "opponent_restriction"
    # "Activated abilities … can't be activated" lock
    ACTIVATED_RESTRICTION  = "activated_restriction"
    # Opponents' spells cost more to cast
    TAX_EFFECT             = "tax_effect"
    # Opponents' permanents / lands enter the battlefield tapped
    ENTERS_TAPPED_OPPONENT = "enters_tapped_opponent"

    # =========================================================================
    # 17. Utility roles — removal, tutors, interaction, combat tricks
    #     (utility.py PRODUCER_MAP keys)
    # =========================================================================

    # ── Removal ───────────────────────────────────────────────────────────────
    # Destroy or exile a single permanent
    TARGETED_REMOVAL       = "targeted_removal"
    # Single-target direct damage (Lightning Bolt, etc.)
    BURN                   = "burn"
    # -1/-1 counter application or -X/-X debuff
    WITHER                 = "wither"
    # Return a permanent to hand
    BOUNCE                 = "bounce"
    # Destroy / exile all (or all of a type)
    SWEEPER                = "sweeper"

    # ── Tutors ────────────────────────────────────────────────────────────────
    TUTOR_CREATURE         = "tutor_creature"
    TUTOR_ARTIFACT         = "tutor_artifact"
    # Any-card generic tutor (Demonic Tutor, Vampiric Tutor)
    TUTOR_ANY              = "tutor_any"

    # ── Interaction ───────────────────────────────────────────────────────────
    # Unconditional counter (Counterspell, Force of Will, Mana Drain)
    COUNTERSPELL_HARD      = "counterspell_hard"
    # Type- or cost-conditioned counter (Negate, Swan Song, Spell Pierce)
    COUNTERSPELL_CONDITIONAL = "counterspell_conditional"
    # Target-change effect acting as a soft counter (Deflecting Swat)
    COUNTERSPELL_REDIRECT  = "counterspell_redirect"
    # Instant-speed indestructible / hexproof / phasing protection
    PROTECTION             = "protection"

    # ── Combat tricks ─────────────────────────────────────────────────────────
    # Temporary evasion keyword grant (flying, menace, shadow, can't-be-blocked)
    EVASION_GRANT          = "evasion_grant"
    # Pump (+X/+X) or damage-keyword grant (trample, deathtouch, double strike)
    COMBAT_TRICKS          = "combat_tricks"
