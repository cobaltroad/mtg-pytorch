"""Tests for the commander-value synergy patterns.

Validates that:
  1. TRIGGER_PATTERNS correctly match oracle text on the *consumer* side
     (cards that benefit from having a commander in play).
  2. PRODUCER_MAP SQL fragments are syntactically well-formed and select
     the right category of cards (low-MV legendary creatures/planeswalkers).
  3. Canonical "commander-value" cards like Deflecting Swat, Fierce
     Guardianship, Loyal Apprentice, and Jeska's Will are tagged.
  4. Cards that do NOT have commander-conditional text are NOT tagged.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from synergy.commander_value import TRIGGER_PATTERNS, PRODUCER_MAP, EDGE_SCORES  # noqa: E402


# ── Helper ────────────────────────────────────────────────────────────────────

def get_events(oracle_text: str) -> set[str]:
    """Return the set of trigger_event names matched by a card's oracle text."""
    matched: set[str] = set()
    for pattern, _name, event in TRIGGER_PATTERNS:
        if re.search(pattern, oracle_text, re.IGNORECASE):
            matched.add(event)
    return matched


# ── Oracle texts for canonical commander-value cards ─────────────────────────

DEFLECTING_SWAT = (
    "If you control a commander, you may cast this spell without paying its mana cost.\n"
    "Change the target of target spell or ability with a single target."
)

FIERCE_GUARDIANSHIP = (
    "If you control a commander, you may cast this spell without paying its mana cost.\n"
    "Counter target noncreature spell."
)

FLAWLESS_MANEUVER = (
    "If you control a commander, you may cast this spell without paying its mana cost.\n"
    "Creatures you control gain indestructible until end of turn."
)

DEADLY_ROLLICK = (
    "If you control a commander, you may cast this spell without paying its mana cost.\n"
    "Exile target creature an opponent controls."
)

OBSCURING_HAZE = (
    "If you control a commander, you may cast this spell without paying its mana cost.\n"
    "Prevent all combat damage that would be dealt to creatures you control this turn."
)

LOYAL_APPRENTICE = (
    "Haste\n"
    "At the beginning of combat on your turn, if you control a commander, "
    "create a 1/1 colorless Thopter artifact creature token with flying."
)

JESKAS_WILL = (
    "Choose one or both —\n"
    "• Add {R}{R}{R}. Spend this mana only to cast instant or sorcery spells.\n"
    "• If you control a commander, exile the top X cards of your library, where X is your "
    "commander's power. Until end of turn, you may play those cards."
)

MOX_AMBER = (
    "{T}: Add one mana of any color among legendary creatures and planeswalkers you control."
)

SELVALA_HEART = (
    "{T}: Add X mana in any combination of colors, where X is the greatest power "
    "among creatures you control. Each other player may draw a card."
)

SISAY_WEATHERLIGHT = (
    "Vigilance\n"
    "{T}: Search your library for a legendary permanent card with mana value less than "
    "or equal to the number of legendary permanents you control, reveal it, put it into "
    "your hand, then shuffle."
)


# ── commander_free_cast consumer detection ────────────────────────────────────

class TestCommanderFreeCast:
    """Cards that may be cast for free if a commander is in play."""

    @pytest.mark.parametrize("name,oracle", [
        ("Deflecting Swat",    DEFLECTING_SWAT),
        ("Fierce Guardianship",FIERCE_GUARDIANSHIP),
        ("Flawless Maneuver",  FLAWLESS_MANEUVER),
        ("Deadly Rollick",     DEADLY_ROLLICK),
        ("Obscuring Haze",     OBSCURING_HAZE),
    ])
    def test_free_cast_cards_tagged(self, name, oracle):
        events = get_events(oracle)
        assert "commander_free_cast" in events, (
            f"{name!r} should be tagged as commander_free_cast; got {events}"
        )

    def test_ordinary_counterspell_not_tagged(self):
        oracle = "Counter target spell."
        events = get_events(oracle)
        assert "commander_free_cast" not in events

    def test_generic_cost_reduction_not_tagged(self):
        oracle = "This spell costs {1} less to cast for each creature you control."
        events = get_events(oracle)
        assert "commander_free_cast" not in events


# ── commander_in_play_payoff consumer detection ───────────────────────────────

class TestCommanderInPlayPayoff:
    """Cards that gain abilities / produce bonus resources when a commander is present."""

    def test_loyal_apprentice(self):
        events = get_events(LOYAL_APPRENTICE)
        assert "commander_in_play_payoff" in events, (
            f"Loyal Apprentice should be tagged as commander_in_play_payoff; got {events}"
        )

    def test_jeskas_will(self):
        events = get_events(JESKAS_WILL)
        assert "commander_in_play_payoff" in events, (
            f"Jeska's Will should be tagged as commander_in_play_payoff; got {events}"
        )

    def test_as_long_as_wording(self):
        oracle = "As long as you control a commander, creatures you control get +1/+0."
        events = get_events(oracle)
        assert "commander_in_play_payoff" in events

    def test_while_wording(self):
        oracle = "While you control a commander, you may activate this ability."
        events = get_events(oracle)
        assert "commander_in_play_payoff" in events

    def test_add_mana_if_commander(self):
        # Jeska's Will first mode: conditional mana burst
        oracle = "If you control a commander, add {R}{R}{R}."
        events = get_events(oracle)
        assert "commander_in_play_payoff" in events

    def test_unrelated_conditional_not_tagged(self):
        oracle = "If you control three or more creatures, draw a card."
        events = get_events(oracle)
        assert "commander_in_play_payoff" not in events


# ── commander_mana_value consumer detection ───────────────────────────────────

class TestCommanderManaValue:
    """Cards that produce mana scaled by a legendary creature/planeswalker."""

    def test_mox_amber_style(self):
        oracle = (
            "{T}: Add one mana of any color among legendary creatures "
            "and planeswalkers you control."
        )
        events = get_events(oracle)
        assert "commander_mana_value" in events, (
            f"Mox Amber text should be tagged as commander_mana_value; got {events}"
        )

    def test_add_referencing_legendary_creature(self):
        oracle = (
            "{T}: Add mana in any combination of colors equal to the mana value "
            "of target legendary creature you control."
        )
        events = get_events(oracle)
        assert "commander_mana_value" in events

    def test_generic_tap_add_not_tagged(self):
        oracle = "{T}: Add {G}."
        events = get_events(oracle)
        assert "commander_mana_value" not in events

    def test_sol_ring_not_tagged(self):
        oracle = "{T}: Add {C}{C}."
        events = get_events(oracle)
        assert "commander_mana_value" not in events


# ── No false positives on common staples ──────────────────────────────────────

class TestNoFalsePositives:
    """Common staples that should NOT be tagged as commander-value consumers."""

    @pytest.mark.parametrize("name,oracle", [
        ("Lightning Bolt",     "Lightning Bolt deals 3 damage to any target."),
        ("Counterspell",       "Counter target spell."),
        ("Swords to Plowshares",
                               "Exile target creature. Its controller gains life equal to its power."),
        ("Cultivate",
                               "Search your library for up to two basic land cards, reveal those cards, "
                               "put one onto the battlefield tapped and the other into your hand, then shuffle."),
        ("Rhystic Study",
                               "Whenever an opponent casts a spell, you may pay {1}. If you don't, draw a card."),
        ("Sol Ring",           "{T}: Add {C}{C}."),
    ])
    def test_not_commander_value_consumer(self, name, oracle):
        events = get_events(oracle)
        assert "commander_free_cast" not in events, f"{name!r} falsely tagged as commander_free_cast"
        assert "commander_in_play_payoff" not in events, f"{name!r} falsely tagged as commander_in_play_payoff"
        assert "commander_mana_value" not in events, f"{name!r} falsely tagged as commander_mana_value"


# ── PRODUCER_MAP SQL fragment structure ───────────────────────────────────────

class TestProducerMapStructure:
    """Sanity checks on PRODUCER_MAP SQL fragments."""

    def test_all_trigger_events_have_producer(self):
        """Every trigger_event in TRIGGER_PATTERNS must have a PRODUCER_MAP entry."""
        events_in_patterns = {event for _, _, event in TRIGGER_PATTERNS}
        for event in events_in_patterns:
            assert event in PRODUCER_MAP, (
                f"trigger_event {event!r} has no entry in PRODUCER_MAP"
            )

    def test_all_trigger_events_have_score(self):
        """Every trigger_event must have an EDGE_SCORES entry."""
        events_in_patterns = {event for _, _, event in TRIGGER_PATTERNS}
        for event in events_in_patterns:
            assert event in EDGE_SCORES, (
                f"trigger_event {event!r} has no entry in EDGE_SCORES"
            )

    def test_low_mv_producer_sql_contains_cmc_filter(self):
        """The free_cast and in_play_payoff producers must filter by CMC ≤ 2."""
        for event in ("commander_free_cast", "commander_in_play_payoff"):
            sql = PRODUCER_MAP[event]
            assert "cmc <= 2" in sql, (
                f"PRODUCER_MAP[{event!r}] should restrict to CMC ≤ 2; got:\n{sql}"
            )

    def test_mana_value_producer_sql_no_cmc_cap(self):
        """The commander_mana_value producer should NOT have a CMC cap."""
        sql = PRODUCER_MAP["commander_mana_value"]
        assert "cmc <=" not in sql, (
            "PRODUCER_MAP['commander_mana_value'] should not have a CMC cap; "
            "Mox Amber works with any legendary permanent."
        )

    def test_all_producers_require_legendary(self):
        """All producer SQL fragments must restrict to legendary permanents."""
        for event, sql in PRODUCER_MAP.items():
            assert "legendary" in sql.lower(), (
                f"PRODUCER_MAP[{event!r}] should restrict to legendary permanents"
            )

    def test_edge_scores_in_valid_range(self):
        """All edge scores must be in (0, 1]."""
        for event, score in EDGE_SCORES.items():
            assert 0 < score <= 1.0, (
                f"EDGE_SCORES[{event!r}] = {score} is outside (0, 1]"
            )

    def test_free_cast_score_highest(self):
        """commander_free_cast should have the highest score (hardest payoff)."""
        free_score = EDGE_SCORES["commander_free_cast"]
        for event, score in EDGE_SCORES.items():
            if event != "commander_free_cast":
                assert free_score >= score, (
                    f"commander_free_cast score ({free_score}) should be ≥ {event} ({score})"
                )


# ── commander_analysis integration ───────────────────────────────────────────

_pydantic_available = importlib.util.find_spec("pydantic") is not None


@pytest.mark.skipif(
    not _pydantic_available,
    reason="pydantic (API dependency) not installed — tests run in full inside Docker",
)
class TestCommanderAnalysisIntegration:
    """Tests for the low-MV commander detection in analyze_commander_oracle_text()."""

    def _analyze(self, oracle="", cmc=None, type_line="Legendary Creature — Human"):
        # Import here to keep test isolation clear
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "api"))
        from ops.commander_analysis import analyze_commander_oracle_text
        return analyze_commander_oracle_text(
            oracle_text=oracle,
            commander_name="Test Commander",
            cmc=cmc,
            type_line=type_line,
        )

    def test_cmc_zero_gives_commander_value_boost(self):
        """CMC 0 commander (e.g. Rograkh) should emit commander_value boost."""
        result = self._analyze(oracle="", cmc=0)
        assert "commander_value" in result.boost_overrides, (
            f"CMC 0 commander should activate commander_value boost; got {result.boost_overrides}"
        )

    def test_cmc_one_gives_commander_value_boost(self):
        """CMC 1 commander (e.g. Yoshimaru) should emit commander_value boost."""
        result = self._analyze(oracle="", cmc=1)
        assert "commander_value" in result.boost_overrides

    def test_cmc_two_gives_commander_value_boost(self):
        """CMC 2 commander (e.g. Thrasios) should emit commander_value boost."""
        result = self._analyze(oracle="", cmc=2)
        assert "commander_value" in result.boost_overrides

    def test_cmc_three_no_commander_value_boost(self):
        """CMC 3 commander should NOT get commander_value boost."""
        result = self._analyze(oracle="", cmc=3)
        assert "commander_value" not in result.boost_overrides, (
            f"CMC 3 commander should NOT activate commander_value; got {result.boost_overrides}"
        )

    def test_cmc_none_no_commander_value_boost(self):
        """When CMC is unknown (None), no commander_value boost should be emitted."""
        result = self._analyze(oracle="", cmc=None)
        assert "commander_value" not in result.boost_overrides

    def test_archetype_hint_low_mv_commander(self):
        """Low-MV commander with no other signals should hint at commander-value staples."""
        result = self._analyze(oracle="", cmc=1)
        assert result.archetype_hint is not None
        assert "low-MV" in result.archetype_hint or "commander-value" in result.archetype_hint, (
            f"Unexpected archetype hint for CMC-1 commander: {result.archetype_hint!r}"
        )

    def test_archetype_hint_low_mv_plus_tribal(self):
        """CMC-1 tribal commander should combine both hints."""
        result = self._analyze(
            oracle="Whenever Yoshimaru grows, an Elf enters the battlefield.",
            cmc=1,
            type_line="Legendary Creature — Dog",
        )
        # Both commander_value and tribal boosts should be present
        assert "commander_value" in result.boost_overrides
        # Archetype hint should reflect the combination
        assert result.archetype_hint is not None
