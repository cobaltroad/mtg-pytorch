"""SQL fragments keyed by mechanic role, split into producer and consumer maps.

Design principle
----------------
Every pattern key belongs to exactly one side of a commander's game plan:

  PRODUCER  — the commander *outputs* a resource or trigger as a primary
               effect.  The SQL selects cards that amplify or pay off from
               that output.
               Key encodes what the commander generates; SQL finds consumers.
               e.g. Tyvar places +1/+1 counters → key "counter_placement"
                    → SQL returns doublers, proliferate, power-matters payoffs.

  CONSUMER  — the commander *needs* the deck to supply a resource or card
               type in order for its ability to fire or scale.
               Key encodes what the commander demands; SQL finds providers.
               e.g. Tyvar triggers off mana abilities → key "mana_dork"
                    → SQL returns creatures that tap for mana.

Tyvar the Bellicose {2}{B}{G}  —  5/4 Legendary Creature — Elf Warrior
  "Whenever one or more Elves you control attack, they gain deathtouch until
   end of turn."
  "Each creature you control has 'Whenever a mana ability of this creature
   resolves, put a number of +1/+1 counters on it equal to the amount of mana
   this creature produced.  This ability triggers only once each turn.'"

  PRODUCER keys: counter_placement  (Tyvar outputs counters onto mana dorks)
  CONSUMER keys: mana_dork          (Tyvar needs creatures that tap for mana)
                 attack_trigger     (Tyvar needs a deck that wants to attack)
                 tribal_elf         (Tyvar needs Elves to trigger his ability)
"""

from __future__ import annotations

from synergy.triggered_ability import PATTERNS as _triggered_abilities
from synergy.activated_ability import PATTERNS as _activated_abilities
from synergy.spell import PATTERNS as _spells
from synergy.combat import PATTERNS as _combat_tricks
from synergy.tribal import tribal_sql, TRIBES as _tribes


# _spells values are raw SQL strings; _triggered_abilities / _activated_abilities
# values are list[str] key groups.  They have different shapes so cannot share a
# dict — _family_sql() expects list[str].  Reference _spells directly below.
PATTERNS = {**_triggered_abilities, **_activated_abilities, **_combat_tricks}


def _family_sql(family_key: str) -> str:
    """Generate SQL that selects cards tagged with any pattern in *family_key*.

    Queries card_abilities.trigger_event so the result is driven entirely by
    what tag.py wrote — no oracle_text LIKE chains needed here.
    """
    keys = PATTERNS[family_key]
    in_list = ", ".join(f"'{k}'" for k in keys)
    return (
        f"id IN ("
        f"  SELECT card_id FROM card_abilities"
        f"  WHERE trigger_event IN ({in_list})"
        f")"
    )


PATTERN_KEY_TO_PRODUCER_SQL: dict[str, str] = {

    # ── PRODUCER: deck needs lifegain payoff cards ────────────────────────────
    # A commander that outputs lifegain (e.g. Sythis, Oloro) wants cards that
    # consume life-gain triggers: Ajani's Pridemate, Archangel of Thune, etc.
    "lifegain_producer": _family_sql("lifegain_trigger"),

    # ── PRODUCER: deck needs draw payoff cards ────────────────────────────────
    # A commander that draws cards as a primary output (e.g. Sythis, Edric)
    # wants cards that consume draw triggers: Niv-Mizzet, Psychosis Crawler, etc.
    "draw_producer": _family_sql("draw_trigger"),

    # ── PRODUCER: counter synergy from commanders that place counters ──────────
    # Any commander whose oracle text places +1/+1 counters as a primary output
    # wants the same counter consumer package.
    "counter_placement": _family_sql("counter_trigger"),

    # ── PRODUCER: high-MV payoff commanders want high mana value spells ───────
    # A commander that scales damage from revealed card MV (e.g. Yuriko) wants
    # the deck stacked with the highest-CMC spells possible.
    "high_mv_payoff": _spells["high_mv"],

    # ── PRODUCER: cascade commanders want cast-from-exile payoff cards ────────
    # A commander with cascade (e.g. Yidris, Maelstrom Wanderer, Abaddon) or
    # that otherwise exiles cards to cast them wants cards that trigger or scale
    # off spells being cast from exile: Prosper, Faldorn, etc.
    "cascade": _spells["cast_from_exile_payoff"],

    # ── PRODUCER: creature token generators want ETB payoff cards ─────────────
    # A commander that outputs creature tokens (e.g. Krenko, Mob Boss) wants
    # cards that fire when creatures enter: Purphoros, Impact Tremors, Anointed
    # Procession, etc.  Both token and non-token ETB consumers qualify.
    "creature_token_generator": _family_sql("creature_etb"),
}

PATTERN_KEY_TO_CONSUMER_SQL: dict[str, str] = {

    # ── CONSUMER: deck needs tribal creatures (all supported tribes) ──────────
    # Any commander with a tribal payoff (e.g. Tyvar for Elves) wants the deck
    # filled with creatures of that tribe (and changelings).
    **{f"tribal_{_tribe}": tribal_sql(_tribe) for _tribe, _ in _tribes},

    # ── CONSUMER: deck needs mana-ability creatures ───────────────────────────
    # Tyvar's second ability triggers off mana abilities — the deck produces
    # the game state he needs by running creatures that tap for mana.
    "mana_dork": f"type_line ILIKE '%%Creature%%' AND {_family_sql('mana_producer')}",

    # ── CONSUMER: attack triggers want cards that encourage combat ────────────
    # A commander with an attack trigger (e.g. Tyvar, Isshin, Gahiji) benefits
    # most from a deck full of evasion granters, keyword enablers, and pump
    # spells — anything that makes attacking creatures more dangerous or harder
    # to profitably block.
    "attack_trigger": _family_sql("combat_tricks"),
    "combat_damage_to_player": _family_sql("combat_tricks"),

    # ── CONSUMER: creature token generators want sac outlets ─────────────────
    # A commander that floods the board with tokens (e.g. Krenko) wants sac
    # outlets to convert that board presence into damage, draw, or mana:
    # Ashnod's Altar, Goblin Bombardment, Viscera Seer, etc.
    "creature_token_generator": _family_sql("sac_outlet"),

    # ── CONSUMER: deck needs spells of the type the commander cares about ─────
    # A commander with a cast trigger (e.g. Sythis) wants the deck filled with
    # the triggering spell type — enchantments for Sythis, creatures for Beast
    # Whisperer, etc.  SQL comes from _spells directly (raw type_line filters).
    "cast_trigger_enchantment":     _spells["spell_enchantment"],
    "cast_trigger_creature":        _spells["spell_creature"],
    "cast_trigger_artifact":        _spells["spell_artifact"],
    "cast_trigger_instant_sorcery": _spells["spell_instant_sorcery"],
    "cast_trigger_historic":        _spells["spell_historic"],
    "cast_trigger_aura_equipment":  _spells["spell_aura_equipment"],

    # ── CONSUMER: color-based cast triggers want spells of that color ─────────
    # A commander whose trigger fires on a specific color (e.g. K'rrik for black,
    # Chandra for red, Aragorn for multicolor) wants the deck filled with spells
    # of the relevant color.  Generated from the color map to stay DRY.
    **{
        f"cast_trigger_{color}": _spells[f"spell_{color}"]
        for color in ("white", "blue", "black", "red", "green", "colorless")
    },
}
