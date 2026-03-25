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

from synergy.trigger_patterns import PATTERN_FAMILIES


def _family_sql(family_key: str) -> str:
    """Generate SQL that selects cards tagged with any pattern in *family_key*.

    Queries card_abilities.trigger_event so the result is driven entirely by
    what tag.py wrote — no oracle_text LIKE chains needed here.
    """
    keys = PATTERN_FAMILIES[family_key]
    in_list = ", ".join(f"'{k}'" for k in keys)
    return (
        f"card_id IN ("
        f"  SELECT card_id FROM card_abilities"
        f"  WHERE trigger_event IN ({in_list})"
        f")"
    )


PATTERN_KEY_TO_CONSUMER_SQL: dict[str, str] = {

    # ── CONSUMER: Tyvar needs Elves ───────────────────────────────────────────
    # "Whenever one or more Elves you control attack …"
    # The deck should be packed with Elf creatures so Tyvar's first ability
    # fires as often as possible and the deathtouch grant is relevant.
    "tribal_elf": (
        "lower(type_line) LIKE '%elf%'"
        " AND lower(type_line) LIKE '%creature%'"
    ),

    # ── CONSUMER: Tyvar needs mana dorks ──────────────────────────────────────
    # "Whenever a mana ability of this creature resolves …"
    # Any creature that can tap to produce mana triggers Tyvar's second ability,
    # growing itself by the amount of mana it made.  Llanowar Elves tapping for
    # {G} gets one counter; a Priest of Titania tapping for {G}{G}{G}{G} gets
    # four.  The deck wants as many mana-ability creatures as possible.
    "mana_dork": (
        "lower(type_line) LIKE '%creature%'"
        " AND ("
        "   lower(oracle_text) LIKE '%add {g}%'"
        "   OR lower(oracle_text) LIKE '%add {b}%'"
        "   OR lower(oracle_text) LIKE '%add {c}%'"
        "   OR lower(oracle_text) LIKE '%add one mana%'"
        "   OR lower(oracle_text) LIKE '%add mana%'"
        "   OR lower(oracle_text) LIKE '%add an amount of%'"
        "   OR lower(oracle_text) LIKE '%produces mana%'"
        "   OR lower(oracle_text) LIKE '%mana ability%'"
        ")"
    ),
}

PATTERN_KEY_TO_PRODUCER_SQL: dict[str, str] = {

    # ── PRODUCER: Tyvar produces attack triggers ───────────────────────────────
    # "they gain deathtouch until end of turn"
    # Tyvar turns every Elf attack into a deathtouch assault.  The deck wants
    # cards that reward this: combat-damage payoffs, trample enablers (deathtouch
    # + trample = one damage kills, rest tramples), and cards that care about
    # creatures dealing combat damage or connecting.
    "attack_trigger": _family_sql("attack_trigger"),

    # ── PRODUCER: Tyvar produces +1/+1 counters ───────────────────────────────
    # "put a number of +1/+1 counters on it equal to the amount of mana"
    # Tyvar grows every mana dork every turn.  The deck wants cards that amplify
    # counter accumulation: counter doublers, proliferate, cards that care about
    # +1/+1 counters or creatures with large power (which the counters build).
    "counter_trigger": _family_sql("counter_trigger"),
}
