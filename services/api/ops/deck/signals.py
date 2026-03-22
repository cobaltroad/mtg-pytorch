"""DeckSignals: what does this commander's deck want?

Extracted once from the commander and passed to every scorer.
No card-type assumptions — scorers decide what cards serve each signal.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_CMD_WANTS_ATTACK_RE = re.compile(r"\battack(s|ing|ers?)?\b|\bcombat damage\b", re.I)


@dataclass(frozen=True)
class DeckSignals:
    wants_attack: bool
    tribal_types: frozenset[str]
    real_colors: frozenset[str]       # color_identity minus colorless
    active_boosts: frozenset[str]     # from commander analysis (e.g. "mana_producers")


def build_signals(
    commander_oracle_text: str,
    color_identity: list[str],
    tribal_types: frozenset[str],
    boost_overrides: list[str] | None,
) -> DeckSignals:
    return DeckSignals(
        wants_attack=bool(_CMD_WANTS_ATTACK_RE.search(commander_oracle_text)),
        tribal_types=frozenset(tribal_types),
        real_colors=frozenset(c for c in color_identity if c != "C"),
        active_boosts=frozenset(boost_overrides or []),
    )
