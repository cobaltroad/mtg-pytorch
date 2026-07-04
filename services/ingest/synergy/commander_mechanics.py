"""SQL fragments keyed by mechanic role, split into producer and consumer maps.

Design principle
----------------
Every pattern key belongs to exactly one side of a commander's game plan:

  PRODUCER  — the commander *outputs* a resource or trigger as a primary
               effect.  The SQL selects cards that amplify or pay off from
               that output.
               Decompose keys (what fires on the commander's oracle text) are
               translated to deck keys (what the deck needs) via
               PRODUCER_DECOMPOSE_TO_DECK_KEY.  PATTERN_KEY_TO_PRODUCER_SQL
               is indexed by deck key, not decompose key.
               e.g. Tyvar fires decompose key "counter_placement"
                    → deck key "counter_trigger"
                    → SQL returns doublers, proliferate, power-matters payoffs.

  CONSUMER  — the commander *needs* the deck to supply a resource or card
               type in order for its ability to fire or scale.
               Key encodes what the commander demands; SQL finds providers.
               Consumer deck key == decompose key (no translation needed).
               e.g. Tyvar triggers off mana abilities → key "mana_dork"
                    → SQL returns creatures that tap for mana.

Tyvar the Bellicose {2}{B}{G}  —  5/4 Legendary Creature — Elf Warrior
  "Whenever one or more Elves you control attack, they gain deathtouch until
   end of turn."
  "Each creature you control has 'Whenever a mana ability of this creature
   resolves, put a number of +1/+1 counters on it equal to the amount of mana
   this creature produced.  This ability triggers only once each turn.'"

  PRODUCER decompose key: counter_placement
           → deck key:    counter_trigger   (counter-trigger amplifiers)
  CONSUMER keys: mana_dork                  (creatures that tap for mana)
                 attack_trigger             (deck that wants to attack)
                 tribal_elf                 (Elves to trigger his ability)
"""

from __future__ import annotations

from synergy.triggered_ability import PATTERNS as _triggered_abilities
from synergy.activated_ability import PATTERNS as _activated_abilities
from synergy.spell import PATTERNS as _spells
from synergy.combat import PATTERNS as _combat_tricks
from synergy.tribal import tribal_sql, TRIBES as _tribes
from synergy.staples.treasure import SQL as _TREASURE_SQL
from synergy.staples.token import SQL as _TOKEN_SQL
from mtg_sql.staples.removal import (
    DESTROY as _REMOVAL_DESTROY,
    DAMAGE as _REMOVAL_DAMAGE,
)


# _spells values are raw SQL strings; _triggered_abilities / _activated_abilities
# values are list[str] key groups.  They have different shapes so cannot share a
# dict — _family_sql() expects list[str].  Reference _spells directly below.
PATTERNS = {**_triggered_abilities, **_activated_abilities, **_combat_tricks}


def family_sql(family_key: str) -> str:
    """Generate SQL that selects cards tagged with any pattern in *family_key*.

    Queries card_abilities.trigger_event so the result is driven entirely by
    what tag.py wrote — no oracle_text LIKE chains needed here.

    Public so that other modules (e.g. stages/mechanics.py) can import
    this as the single canonical implementation rather than duplicating it.
    """
    keys = PATTERNS[family_key]
    in_list = ", ".join(f"'{k}'" for k in keys)
    return (
        f"id IN ("
        f"  SELECT card_id FROM card_abilities"
        f"  WHERE trigger_event IN ({in_list})"
        f")"
    )


# Internal alias used throughout this module
_family_sql = family_sql


# ── Producer → deck-key translation ─────────────────────────────────────────
# Maps the decompose key that fires on a commander's oracle text to the deck
# key that describes what the *deck* needs to support that commander.
#
# Decompose key (what the commander does)  →  Deck key (what the deck needs)
#
# NOTE: This dict is the single source of truth for the producer relationship.
#   - PATTERN_KEY_TO_PRODUCER_SQL is indexed by deck key (right-hand values).
#   - stages/decompose.py, scripts/export_dataset_commanders.py, and
#     scripts/eval_decomposition.py all go through this dict first.
#   - A copy of this dict and DECK_KEY_LABELS is maintained in
#     services/api/main.py (no shared import path); keep them in sync.
PRODUCER_DECOMPOSE_TO_DECK_KEY: dict[str, list[str]] = {
    "lifegain_producer": ["lifegain_trigger"],
    "draw_producer": ["draw_trigger"],
    "counter_placement": ["counter_trigger"],
    "high_mv_payoff": ["high_mv_payoff"],
    "cascade": ["cast_from_exile_payoff"],
    # token generators need ETB payoffs (producer) AND sac outlets (tokens are the fodder)
    "creature_token_generator": ["creature_etb_payoff", "sac_outlet"],
    # attack/combat-damage commanders need combat tricks in the deck
    "attack_trigger": ["combat_tricks"],
    "combat_damage_to_player": ["combat_tricks"],
    # death-trigger commanders need sac outlets, self-sacrificing fodder, and
    # low-toughness creatures (die easily, generating triggers without sac outlets)
    "death_trigger": [
        "sac_outlet",
        "sacrifice_fodder",
        "toughness_1_creatures",
    ],
    # sacrifice-payoff commanders need sac outlets + treasure generators + token generators
    "sacrifice_payoff": ["sac_outlet", "treasure_generators", "token_generators"],
    # cast trigger commanders need both amplifiers (other cards with same trigger)
    # and spells of the triggering type as fodder
    "cast_trigger_enchantment": ["enchantment_cast", "spell_enchantment"],
    "cast_trigger_creature": ["creature_cast", "spell_creature"],
    "cast_trigger_artifact": ["artifact_cast", "spell_artifact"],
    "cast_trigger_instant_sorcery": ["instant_sorcery_cast", "spell_instant_sorcery"],
    "cast_trigger_historic": ["historic_cast", "spell_historic"],
    "cast_trigger_aura_equipment": ["aura_equipment_cast", "spell_aura_equipment"],
    # color-based cast triggers need spells of that color (no amplifier category exists)
    **{
        f"cast_trigger_{c}": [f"spell_{c}"]
        for c in ("white", "blue", "black", "red", "green", "colorless")
    },
}


# ── Human-readable labels for deck keys ──────────────────────────────────────
# Used by the API and UI to display the producer→consumer relationship.
# Producer deck keys (no oracle pattern fires these on the commander):
# Consumer deck keys (key == decompose key) borrow the ORACLE_PATTERNS label
# style from stages/decompose.py.
DECK_KEY_LABELS: dict[str, str] = {
    # ── producer deck keys ────────────────────────────────────────────────────
    "lifegain_trigger": "Lifegain trigger payoffs",
    "draw_trigger": "Draw trigger payoffs",
    "counter_trigger": "Counter trigger payoffs",
    "high_mv_payoff": "High mana value payoffs",
    "cast_from_exile_payoff": "Cast-from-exile payoffs",
    "creature_etb_payoff": "Creature ETB payoffs",
    # ── translated consumer deck keys ─────────────────────────────────────────
    "combat_tricks": "Combat tricks (evasion, pump, haste)",
    "sac_outlet": "Sac outlets",
    "sacrifice_fodder": "Self-sacrificing fodder",
    "toughness_1_creatures": "Toughness-1 creatures",
    "destroy_removal": "Destroy removal",
    "damage_removal": "Damage-based removal",
    "treasure_generators": "Treasure generators",
    "token_generators": "Token generators",
    # cast trigger amplifiers (cards that also trigger on the same cast event)
    "enchantment_cast": "Enchantment cast trigger amplifiers",
    "creature_cast": "Creature cast trigger amplifiers",
    "artifact_cast": "Artifact cast trigger amplifiers",
    "instant_sorcery_cast": "Instant/sorcery cast trigger amplifiers",
    "historic_cast": "Historic cast trigger amplifiers",
    "aura_equipment_cast": "Aura/equipment cast trigger amplifiers",
    # spell fodder (spells of the type that trigger the commander)
    "spell_enchantment": "Enchantment spells",
    "spell_creature": "Creature spells",
    "spell_artifact": "Artifact spells",
    "spell_instant_sorcery": "Instant / sorcery spells",
    "spell_historic": "Historic spells",
    "spell_aura_equipment": "Aura / equipment spells",
    "mana_dork": "Mana ability creatures",
    "trigger_doubling": "Creatures with attack-triggered abilities",
    "token_generator": "Token doublers and token creation payoffs",
    "artifact_token_generator": "Artifact ETB and graveyard payoffs (non-creature tokens)",
    "proliferate_matters": "Counter-bearing permanents (proliferate targets)",
    # color spell fodder (deck key for color-based cast-trigger commanders)
    **{
        f"spell_{c}": f"{c.title()} spells"
        for c in ("white", "blue", "black", "red", "green", "colorless")
    },
    **{
        f"tribal_{t}": f"{t.title()} tribal creatures"
        for t in (
            "elf",
            "dragon",
            "zombie",
            "vampire",
            "eldrazi",
            "human",
            "dinosaur",
            "goblin",
            "angel",
            "pirate",
            "wizard",
            "assassin",
            "merfolk",
            "cat",
            "sliver",
            "wolf",
            "demon",
            "ninja",
            "squirrel",
            "elemental",
            "dog",
            "spirit",
            "knight",
            "horror",
            "faerie",
            "dwarf",
        )
    },
}


PATTERN_KEY_TO_PRODUCER_SQL: dict[str, str] = {
    # ── deck keys: cast-trigger amplifiers ────────────────────────────────────
    # Commander fires a decompose cast-trigger key (e.g. cast_trigger_enchantment).
    # Deck needs cards that ALSO trigger on the same spell type — amplifying the
    # effect stack each time you cast a spell of the relevant type.
    "enchantment_cast":     _family_sql("enchantment_cast"),
    "creature_cast":        _family_sql("creature_cast"),
    "artifact_cast":        _family_sql("artifact_cast"),
    "instant_sorcery_cast": _family_sql("instant_sorcery_cast"),
    "historic_cast":        _family_sql("historic_cast"),
    "aura_equipment_cast":  _family_sql("aura_equipment_cast"),
    # ── deck key: lifegain_trigger ────────────────────────────────────────────
    # Commander fires decompose key "lifegain_producer" (outputs life gain).
    # Deck needs cards that consume life-gain triggers:
    # Ajani's Pridemate, Archangel of Thune, etc.
    "lifegain_trigger": _family_sql("lifegain_trigger"),
    # ── deck key: draw_trigger ────────────────────────────────────────────────
    # Commander fires decompose key "draw_producer" (draws cards as output).
    # Deck needs cards that consume draw triggers:
    # Niv-Mizzet, Psychosis Crawler, etc.
    "draw_trigger": _family_sql("draw_trigger"),
    # ── deck key: counter_trigger ─────────────────────────────────────────────
    # Commander fires decompose key "counter_placement" (places +1/+1 counters).
    # Deck needs counter-trigger amplifiers: Hardened Scales, Doubling Season, etc.
    "counter_trigger": _family_sql("counter_trigger"),
    # ── deck key: high_mv_payoff ──────────────────────────────────────────────
    # Commander fires decompose key "high_mv_payoff" (scales from card MV).
    # Deck needs the highest-CMC spells possible (Yuriko, Yennett, etc.).
    "high_mv_payoff": _spells["high_mv"],
    # ── deck key: cast_from_exile_payoff ──────────────────────────────────────
    # Commander fires decompose key "cascade" (exiles and casts extra spells).
    # Deck needs cards that trigger/scale off casting from exile:
    # Prosper, Faldorn, etc.
    "cast_from_exile_payoff": _spells["cast_from_exile_payoff"],
    # ── deck key: creature_etb_payoff ─────────────────────────────────────────
    # Commander fires decompose key "creature_token_generator" (floods board
    # with creature tokens).  Deck needs ETB payoff cards: Purphoros, Impact
    # Tremors, Anointed Procession, etc.
    # Sac outlets are covered by the CONSUMER entry for "creature_token_generator".
    "creature_etb_payoff": _family_sql("creature_etb"),
    # ── deck keys: death / sacrifice / combat support ─────────────────────────
    # Commanders that output deaths, sac events, or combat-trigger value need
    # the deck stocked with these support card types.
    "sac_outlet":            _family_sql("sac_outlet"),
    "sacrifice_fodder":      _family_sql("sacrifice_fodder"),
    "toughness_1_creatures": _spells["toughness_1"],
    "combat_tricks":         _family_sql("combat_tricks"),
    "treasure_generators":   _TREASURE_SQL,
    "token_generators":      _TOKEN_SQL,
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
    # ── CONSUMER: trigger-doubling commanders want attack-triggered creatures ────
    # A commander that doubles attack triggers (Isshin, Wulfgar) gets value from
    # creatures whose own abilities fire when they attack — those are the triggers
    # being doubled.
    "trigger_doubling": (
        f"type_line ILIKE '%%Creature%%' AND {_family_sql('attack_trigger')}"
    ),
    # ── CONSUMER: any token generator wants doublers and generic token payoffs ────
    # A commander that creates tokens of any type benefits from cards that
    # double token creation (Doubling Season, Anointed Procession) or trigger
    # whenever tokens are created (Chatterfang, Elspeth Storm Slayer).
    "token_generator": (
        "(oracle_text ~* 'would create .{0,40}tokens?.{0,40}instead'"
        " OR oracle_text ~* 'whenever .{0,30}tokens? (enters?|are created)')"
    ),
    # ── CONSUMER: artifact token generators want artifact ETB/graveyard payoffs ──
    # A commander that creates non-creature tokens (Clue, Blood, Treasure,
    # Mutagen) benefits from cards that trigger on artifacts entering
    # (Reckless Fireweaver, Wily Goblin) or on artifacts going to the graveyard
    # (Disciple of the Vault, Marionette Master).
    "artifact_token_generator": (
        "(oracle_text ~* 'whenever .{0,30}artifact enters'"
        " OR oracle_text ~* 'whenever .{0,30}artifact .{0,30}graveyard')"
    ),
    # ── CONSUMER: creature token generators want sac outlets ─────────────────
    # A commander that floods the board with tokens (e.g. Krenko) wants sac
    # outlets to convert that board presence into damage, draw, or mana:
    # Ashnod's Altar, Goblin Bombardment, Viscera Seer, etc.
    "creature_token_generator": _family_sql("sac_outlet"),
    # ── CONSUMER: death-trigger commanders want sac outlets + self-sac fodder ───
    # A commander that triggers on creature death (e.g. Syr Konrad: "whenever
    # another creature dies, each opponent loses 1 life"; Teysa Karlov: "whenever
    # another nontoken creature you control dies, that creature's abilities
    # trigger an additional time") needs two things:
    #   1. Sac outlets to control when deaths happen (Viscera Seer, Ashnod's Altar)
    #   2. Creatures that self-sacrifice on a schedule — so death triggers fire
    #      without spending a sac outlet (Feldon tokens via sacrifice_eot;
    #      Decayed zombie tokens via keyword_decayed)
    "death_trigger": (
        f"({_family_sql('sac_outlet')}"
        f" OR {_family_sql('sacrifice_fodder')}"
        f" OR {_spells['toughness_1']})"
    ),
    # ── CONSUMER: sacrifice-payoff commanders want sac outlets + fodder ──────────
    # A commander that scales off sacrificing permanents (e.g. Korvold: "whenever
    # you sacrifice a permanent, draw a card and put a +1/+1 counter on Korvold";
    # Prossh: "whenever you cast Prossh, create X 0/1 Kobold tokens, sacrifice
    # a creature to give Prossh +1/+1") needs two things: outlets to sacrifice
    # into (Viscera Seer, Ashnod's Altar) AND a supply of cheap sacrifice fodder.
    # Treasure generators (Dockside Extortionist, Smothering Tithe, Pitiless
    # Plunderer) are ideal fodder — they also produce mana when sacrificed.
    "sacrifice_payoff": f"({_family_sql('sac_outlet')} OR {_TREASURE_SQL} OR {_TOKEN_SQL})",
    # ── CONSUMER: graveyard-casting commanders want the graveyard stocked ─────
    # A commander that casts/plays cards from the graveyard (Muldrotha,
    # Karador, Gisa and Geralf) needs fillers: self-mill, surveil, and
    # entomb-style effects that put specific cards into the graveyard.
    # \y = POSIX word boundary; the put-clause requires an object
    # ("card(s)"/"them"/"it") between put/into so replacement-effect text
    # ("if it would be put into your graveyard") does not match.
    "graveyard_payoff": (
        "(oracle_text ~* '\\ymills?\\y'"
        " OR oracle_text ~* '\\ysurveils?\\y'"
        " OR oracle_text ~* 'put (that card|them|it|.{0,50}cards?) into your graveyard')"
    ),
    # ── CONSUMER: permanents-to-graveyard triggers want outlets + fodder ──────
    # A commander that triggers when permanents hit a graveyard (the
    # graveyard_from_play decompose key) wants to control when that happens:
    # sac outlets plus self-sacrificing fodder — same engine parts as
    # death_trigger, minus the toughness-1 shortcut (permanents, not
    # creature deaths specifically).
    "graveyard_from_play": (
        f"({_family_sql('sac_outlet')} OR {_family_sql('sacrifice_fodder')})"
    ),
    # ── CONSUMER: temporary reanimation wants creatures with ETB value ────────
    # Unearth/encore-style commanders re-animate creatures for one turn —
    # the value is front-loaded, so the deck wants creatures whose ETB
    # triggers do the work before the end-step exile.
    "unearth_encore": (
        f"(type_line ILIKE '%%Creature%%' AND {_family_sql('creature_etb')})"
    ),
    # ── CONSUMER: deck needs spells of the type the commander cares about ─────
    # A commander with a cast trigger (e.g. Sythis) wants the deck filled with
    # the triggering spell type — enchantments for Sythis, creatures for Beast
    # Whisperer, etc.  SQL comes from _spells directly (raw type_line filters).
    "cast_trigger_enchantment": _spells["spell_enchantment"],
    "cast_trigger_creature": _spells["spell_creature"],
    "cast_trigger_artifact": _spells["spell_artifact"],
    "cast_trigger_instant_sorcery": _spells["spell_instant_sorcery"],
    "cast_trigger_historic": _spells["spell_historic"],
    "cast_trigger_aura_equipment": _spells["spell_aura_equipment"],
    # ── CONSUMER: proliferate commanders want counter-bearing permanents ────────
    # A commander that proliferates (Atraxa, Ezuri, Cayth, Brimaz) benefits from
    # a deck full of permanents that already carry counters — +1/+1 counter
    # payoffs, planeswalkers, infect creatures, and counter doublers each get
    # more value from each proliferate trigger.
    "proliferate_matters": (
        f"({_family_sql('counter_trigger')}"
        f" OR type_line ILIKE '%%Planeswalker%%'"
        f" OR oracle_text ILIKE '%%infect%%'"
        f" OR oracle_text ILIKE '%%toxic%%')"
    ),
    # ── CONSUMER: color-based cast triggers want spells of that color ─────────
    # A commander whose trigger fires on a specific color (e.g. K'rrik for black,
    # Chandra for red, Aragorn for multicolor) wants the deck filled with spells
    # of the relevant color.  Generated from the color map to stay DRY.
    **{
        f"cast_trigger_{color}": _spells[f"spell_{color}"]
        for color in ("white", "blue", "black", "red", "green", "colorless")
    },
}
