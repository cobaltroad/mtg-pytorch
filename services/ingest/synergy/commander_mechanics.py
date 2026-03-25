"""Producer SQL fragments keyed by mechanic role.

Design principle
----------------
A commander is either a **consumer** or a **producer** of each mechanic:

  consumer  — the commander *needs* the deck full of these cards
               (e.g. Tyvar wants Elves to attack with)
  producer  — the commander *generates* this trigger / resource
               (e.g. Tyvar grants deathtouch → deck wants things
               that pay off from deathtouch attackers)

Each entry maps a key to a SQL WHERE body that selects the cards which fill
that role in a Tyvar deck.  The key also tells you *why* those cards belong:
does the deck need them as inputs (consumer), does the commander output value
that they amplify (producer)?

Tyvar the Bellicose {2}{B}{G}  —  5/4 Legendary Creature — Elf Warrior
  "Whenever one or more Elves you control attack, they gain deathtouch until
   end of turn."
  "Each creature you control has 'Whenever a mana ability of this creature
   resolves, put a number of +1/+1 counters on it equal to the amount of mana
   this creature produced.  This ability triggers only once each turn.'"
"""

from __future__ import annotations

from synergy.triggered_ability import PATTERNS as _triggered_abilities
from synergy.activated_ability import PATTERNS as _activated_abilities
from synergy.spell import PATTERNS as _spells
from synergy.tribal import tribal_sql


# _spells values are raw SQL strings; _triggered_abilities / _activated_abilities
# values are list[str] key groups.  They have different shapes so cannot share a
# dict — _family_sql() expects list[str].  Reference _spells directly below.
PATTERNS = {**_triggered_abilities, **_activated_abilities}


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

    # ── PRODUCER: deck needs Elf creatures ────────────────────────────────────
    # Tyvar's first ability requires Elves attacking — the deck produces the
    # game state he needs by being full of Elf creatures (and changelings).
    "tribal_elf": tribal_sql("elf"),

    # ── PRODUCER: deck needs mana-ability creatures ───────────────────────────
    # Tyvar's second ability triggers off mana abilities — the deck produces
    # the game state he needs by running creatures that tap for mana.
    "mana_dork": f"type_line ILIKE '%%Creature%%' AND {_family_sql('mana_producer')}",

    # ── PRODUCER: deck needs lifegain payoff cards ────────────────────────────
    # A commander that outputs lifegain (e.g. Sythis, Oloro) wants cards that
    # consume life-gain triggers: Ajani's Pridemate, Archangel of Thune, etc.
    "lifegain_producer": _family_sql("lifegain_trigger"),

    # ── PRODUCER: deck needs draw payoff cards ────────────────────────────────
    # A commander that draws cards as a primary output (e.g. Sythis, Edric)
    # wants cards that consume draw triggers: Niv-Mizzet, Psychosis Crawler, etc.
    "draw_producer": _family_sql("draw_trigger"),

    # ── PRODUCER: deck needs spells of the type the commander cares about ─────
    # A commander with a cast trigger (e.g. Sythis) wants the deck filled with
    # the triggering spell type — enchantments for Sythis, creatures for Beast
    # Whisperer, etc.  SQL comes from _spells directly (raw type_line filters).
    "cast_trigger_enchantment":     _spells["spell_enchantment"],
    "cast_trigger_creature":        _spells["spell_creature"],
    "cast_trigger_artifact":        _spells["spell_artifact"],
    "cast_trigger_instant_sorcery": _spells["spell_instant_sorcery"],
    "cast_trigger_historic":        _spells["spell_historic"],
    "cast_trigger_aura_equipment":  _spells["spell_aura_equipment"],
}

PATTERN_KEY_TO_CONSUMER_SQL: dict[str, str] = {

    # ── CONSUMER: attack triggers benefit from Tyvar's deathtouch grant ───────
    # Tyvar turns every Elf attack into a deathtouch assault.  The deck wants
    # cards that consume/reward attack triggers: combat-damage payoffs, trample
    # enablers, cards that care about creatures connecting.
    "attack_trigger": _family_sql("attack_trigger"),

    # ── CONSUMER: counter synergy consumes Tyvar's +1/+1 counter output ───────
    # Tyvar grows every mana dork every turn.  The deck wants cards that consume
    # counter accumulation: doublers, proliferate, power-matters payoffs.
    "counter_trigger": _family_sql("counter_trigger"),

    # ── CONSUMER: counter synergy from commanders that place counters ──────────
    # Any commander whose oracle text places +1/+1 counters as a primary output
    # wants the same counter consumer package.
    "counter_placement": _family_sql("counter_trigger"),

    # ── CONSUMER: lifegain payoffs ─────────────────────────────────────────────
    # A commander that produces life gain (e.g. Sythis) wants cards that
    # trigger or scale off life being gained: Ajani's Pridemate, Archangel of
    # Thune, Well of Lost Dreams, etc.
    "lifegain_trigger": _family_sql("lifegain_trigger"),

    # ── CONSUMER: draw payoffs ─────────────────────────────────────────────────
    # A commander that draws cards as a primary output (e.g. Sythis) wants
    # cards that trigger or scale off drawing: Niv-Mizzet, Psychosis Crawler,
    # Consecrated Sphinx, etc.
    "draw_trigger": _family_sql("draw_trigger"),
}
