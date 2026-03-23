"""Tests for Phase 2 synergy pattern expansion (issue #66).

Validates the 5 new ability categories added to the pattern library:

* ``cast_creature_spell`` — "whenever you cast a creature spell" payoffs
* ``sac_outlet``          — activated sacrifice-as-cost abilities
* ``landfall_draw``       — landfall-triggered card draw
* ``enchantress``         — enchantress draw (cast/enter enchantment → draw)
* ``adapt_evolve``        — counter-growth keywords (adapt/evolve/graft/bolster/modular/riot)

Also checks the landfall producer fix (fetchlands now matched).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from synergy.events import TRIGGER_PATTERNS as EVENTS_TRIGGERS, PRODUCER_MAP as EVENTS_PRODUCERS
from synergy.deckbuilding import TRIGGER_PATTERNS as DECK_TRIGGERS, PRODUCER_MAP as DECK_PRODUCERS


# ── Helpers ───────────────────────────────────────────────────────────────────

def match_event(patterns: list, event_id: str, text: str) -> bool:
    """Return True if *text* matches the pattern for *event_id*."""
    for pattern, _name, event in patterns:
        if event == event_id and re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def match_producer(producers: dict, event_id: str, oracle: str, type_line: str = "") -> bool:
    """Return True if *oracle*/*type_line* satisfies the producer SQL heuristic.

    We replicate the LIKE / ILIKE semantics with Python ``in`` checks on lowercased text.
    This is a sufficient approximation for unit tests; the DB handles the real matching.
    """
    sql = producers.get(event_id, "")
    # Split on " OR " and evaluate each clause
    for clause in re.split(r"\bOR\b", sql, flags=re.IGNORECASE):
        clause = clause.strip()
        # Extract the LIKE pattern string (text between single quotes)
        m = re.search(r"LIKE '([^']+)'", clause, re.IGNORECASE)
        if not m:
            # AND-compound clause — skip rather than over-engineer; accept false negatives
            continue
        like_pat = m.group(1).replace("%", "")  # strip SQL wildcards → plain substring
        # Determine which field the clause targets
        if "type_line" in clause:
            haystack = type_line.lower()
        else:
            haystack = oracle.lower()
        if like_pat.lower() in haystack:
            return True
    return False


# ── cast_creature_spell ───────────────────────────────────────────────────────

class TestCastCreatureSpell:
    """Whenever you cast a creature spell."""

    def test_beast_whisperer(self):
        oracle = "Whenever you cast a creature spell, draw a card."
        assert match_event(EVENTS_TRIGGERS, "cast_creature_spell", oracle)

    def test_garruk_packleader(self):
        oracle = "Whenever you cast a creature spell with power 3 or greater, draw a card."
        assert match_event(EVENTS_TRIGGERS, "cast_creature_spell", oracle)

    def test_legendary_creature_spell(self):
        oracle = "Whenever you cast a legendary creature spell, draw a card."
        assert match_event(EVENTS_TRIGGERS, "cast_creature_spell", oracle)

    def test_no_false_positive_sorcery(self):
        oracle = "Whenever you cast a sorcery spell, create a 1/1 creature token."
        assert not match_event(EVENTS_TRIGGERS, "cast_creature_spell", oracle)

    def test_producer_creature_card(self):
        assert match_producer(
            EVENTS_PRODUCERS, "cast_creature_spell",
            oracle="Tap: deal 1 damage to any target.",
            type_line="Creature — Human Wizard",
        )

    def test_producer_noncreature_excluded(self):
        assert not match_producer(
            EVENTS_PRODUCERS, "cast_creature_spell",
            oracle="Add {G}.",
            type_line="Instant",
        )


# ── sac_outlet ────────────────────────────────────────────────────────────────

class TestSacOutlet:
    """Activated sacrifice-as-cost abilities."""

    def test_viscera_seer(self):
        oracle = "Sacrifice a creature: Scry 1."
        assert match_event(EVENTS_TRIGGERS, "sac_outlet", oracle)

    def test_altar_of_dementia(self):
        oracle = "Sacrifice a creature: Target player mills cards equal to the sacrificed creature's power."
        assert match_event(EVENTS_TRIGGERS, "sac_outlet", oracle)

    def test_carrion_feeder(self):
        oracle = "Sacrifice a creature: Put a +1/+1 counter on Carrion Feeder."
        assert match_event(EVENTS_TRIGGERS, "sac_outlet", oracle)

    def test_ashnods_altar(self):
        oracle = "Sacrifice a creature: Add {C}{C}."
        assert match_event(EVENTS_TRIGGERS, "sac_outlet", oracle)

    def test_sacrifice_another(self):
        oracle = "Sacrifice another creature: This creature gets +2/+2 until end of turn."
        assert match_event(EVENTS_TRIGGERS, "sac_outlet", oracle)

    def test_no_false_positive_triggered_payoff(self):
        # "whenever you sacrifice" is a triggered payoff, NOT an outlet
        oracle = "Whenever you sacrifice a creature, you gain 1 life."
        assert not match_event(EVENTS_TRIGGERS, "sac_outlet", oracle)

    def test_producer_token_generator(self):
        oracle = "Create three 1/1 white Soldier creature tokens."
        assert match_producer(EVENTS_PRODUCERS, "sac_outlet", oracle)


# ── landfall_draw ─────────────────────────────────────────────────────────────

class TestLandfallDraw:
    """Landfall-triggered draw effects."""

    def test_tatyova(self):
        oracle = "Whenever a land enters the battlefield under your control, you gain 1 life and draw a card."
        assert match_event(EVENTS_TRIGGERS, "landfall_draw", oracle)

    def test_simple_landfall_draw(self):
        oracle = "Whenever a land enters the battlefield, draw a card."
        assert match_event(EVENTS_TRIGGERS, "landfall_draw", oracle)

    def test_no_false_positive_landfall_without_draw(self):
        # Omnath, Locus of Rage makes tokens but doesn't draw
        oracle = "Whenever a land enters the battlefield under your control, create a 5/5 red and green Elemental creature token."
        assert not match_event(EVENTS_TRIGGERS, "landfall_draw", oracle)

    def test_producer_ramp_spell(self):
        oracle = "Search your library for a basic land card, put it onto the battlefield tapped, then shuffle."
        assert match_producer(EVENTS_PRODUCERS, "landfall_draw", oracle)

    def test_producer_fetchland(self):
        # Fetchland text: search for a land card, put it onto the battlefield
        oracle = "Pay 1 life, Sacrifice Scalding Tarn: Search your library for an Island or Mountain card, put it onto the battlefield, then shuffle."
        assert match_producer(EVENTS_PRODUCERS, "landfall_draw", oracle)


# ── landfall producer fix (fetchlands) ───────────────────────────────────────

class TestLandfallProducerFix:
    """Fetchlands are now included in the landfall producer."""

    def test_fetchland_matches_landfall_producer(self):
        fetchland_text = (
            "Pay 1 life, Sacrifice Windswept Heath: Search your library for a Forest or Plains card, "
            "put it onto the battlefield, then shuffle."
        )
        assert match_producer(EVENTS_PRODUCERS, "landfall", fetchland_text)

    def test_rampant_growth_still_matches(self):
        oracle = "Search your library for a basic land card and put it onto the battlefield tapped, then shuffle."
        assert match_producer(EVENTS_PRODUCERS, "landfall", oracle)


# ── enchantress ───────────────────────────────────────────────────────────────

class TestEnchantress:
    """Enchantress draw triggers."""

    def test_sythis(self):
        oracle = "Whenever you cast an enchantment spell, you gain 1 life and draw a card."
        assert match_event(DECK_TRIGGERS, "enchantress", oracle)

    def test_argothian_enchantress(self):
        oracle = "Whenever you cast an Aura spell, draw a card."
        assert match_event(DECK_TRIGGERS, "enchantress", oracle)

    def test_eidolon_of_blossoms(self):
        oracle = "Whenever an enchantment enters the battlefield under your control, draw a card."
        assert match_event(DECK_TRIGGERS, "enchantress", oracle)

    def test_setessan_champion(self):
        oracle = (
            "Whenever an enchantment enters the battlefield under your control, "
            "put a +1/+1 counter on Setessan Champion and draw a card."
        )
        assert match_event(DECK_TRIGGERS, "enchantress", oracle)

    def test_no_false_positive_enchantment_etb_without_draw(self):
        # Enchantment entering that doesn't draw shouldn't match enchantress
        oracle = "Whenever an enchantment enters the battlefield, create a 1/1 Soldier token."
        assert not match_event(DECK_TRIGGERS, "enchantress", oracle)

    def test_producer_enchantment_card(self):
        assert match_producer(
            DECK_PRODUCERS, "enchantress",
            oracle="Enchant creature. Enchanted creature gets +2/+2.",
            type_line="Enchantment — Aura",
        )

    def test_producer_non_enchantment_excluded(self):
        assert not match_producer(
            DECK_PRODUCERS, "enchantress",
            oracle="{T}: Add {G}.",
            type_line="Creature — Elf Druid",
        )


# ── adapt_evolve ──────────────────────────────────────────────────────────────

class TestAdaptEvolve:
    """Counter-growth keywords."""

    def test_evolve(self):
        assert match_event(DECK_TRIGGERS, "adapt_evolve", "Evolve (Whenever a creature enters the battlefield under your control, if that creature has greater power or toughness than this creature, put a +1/+1 counter on this creature.)")

    def test_adapt(self):
        assert match_event(DECK_TRIGGERS, "adapt_evolve", "{4}: Adapt 4. (If this creature has no +1/+1 counters on it, put four +1/+1 counters on it.)")

    def test_graft(self):
        assert match_event(DECK_TRIGGERS, "adapt_evolve", "Graft 2 (This permanent enters the battlefield with two +1/+1 counters on it. Whenever another creature enters the battlefield, you may move a +1/+1 counter from this permanent to it.)")

    def test_bolster(self):
        assert match_event(DECK_TRIGGERS, "adapt_evolve", "Bolster 2 (Choose a creature with the least toughness among creatures you control and put two +1/+1 counters on it.)")

    def test_modular(self):
        assert match_event(DECK_TRIGGERS, "adapt_evolve", "Modular 2 (This enters the battlefield with two +1/+1 counters on it. When it dies, you may put its +1/+1 counters on target artifact creature.)")

    def test_riot(self):
        assert match_event(DECK_TRIGGERS, "adapt_evolve", "Riot (This creature enters the battlefield with your choice of a +1/+1 counter or haste.)")

    def test_producer_proliferate(self):
        oracle = "Proliferate. (Choose any number of permanents and/or players, then give each another counter of each kind already there.)"
        assert match_producer(DECK_PRODUCERS, "adapt_evolve", oracle)

    def test_producer_counter_placement(self):
        oracle = "Put a +1/+1 counter on target creature you control."
        assert match_producer(DECK_PRODUCERS, "adapt_evolve", oracle)
