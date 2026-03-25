"""Lifegain synergy patterns and producer SQL fragments.

Four distinct lifegain-related consumer events are defined here:

* lifegain           — per-instance triggered abilities ("whenever you gain life")
* lifegain_threshold — end-step cumulative checks ("if you gained N or more life this turn")
* lifegain_replacement — replacement effects that modify the amount gained ("if you would gain life")
* lifegain_total     — static/end-step checks against current life total ("if you have 30 or more life")

The two SQL constants are kept in this module and can be imported by other
synergy sub-modules (e.g. tribal cross-synergies) that need to reference the
same lifegain producer pool.
"""

from __future__ import annotations

# ── Shared SQL fragments ──────────────────────────────────────────────────────

# Direct lifegain sources: oracle text that explicitly gains life or grants lifelink.
# Used by lifegain, lifegain_replacement, and lifegain_total as their full producer pool,
# and as the base for lifegain_threshold.
LIFEGAIN_PRODUCER_SQL = (
    "lower(oracle_text) LIKE '%you gain%life%'"
    " OR lower(oracle_text) LIKE '%gain life%'"
    " OR lower(oracle_text) LIKE '%gains life%'"
    " OR lower(oracle_text) LIKE '%lifelink%'"
    " OR lower(oracle_text) LIKE '%life equal to%'"
)

# Extended producer pool for lifegain_threshold: all direct lifegain sources PLUS Food token
# creators.  Every Food token has the intrinsic ability "Sacrifice this artifact: You gain 3
# life." — it is the token itself that gains the life, not the creator card directly.  Creator
# cards are matched here because the tokens they produce are what ultimately enable the 3-life
# gain; two Food tokens clear every standard threshold (4 for Angelic Accord / Valkyrie
# Harbinger, 5 for Resplendent Angel).
LIFEGAIN_THRESHOLD_PRODUCER_SQL = (
    LIFEGAIN_PRODUCER_SQL
    + " OR lower(oracle_text) LIKE '%create%food%'"
    + " OR lower(oracle_text) LIKE '%food token%'"
)

# ── Trigger patterns ──────────────────────────────────────────────────────────

TRIGGER_PATTERNS: list[tuple[str, str, str]] = [
    # Per-instance trigger: fires every time any life is gained.
    (r"when(ever)?\s+(you |a player )?gain(s)? life", "Lifegain trigger", "lifegain"),

    # Cumulative end-step threshold: "if you gained N or more life this turn"
    # (Resplendent Angel, Angelic Accord, Valkyrie Harbinger, Dawn of Hope, etc.)
    (r"if you gained \d+ or more life this turn", "Lifegain threshold trigger", "lifegain_threshold"),

    # Replacement effects: "If you would gain life, you gain that much life plus N instead"
    # (Angel of Vitality, Boon Reflection, Rhox Faithmender, etc.)
    (r"if you would gain life", "Lifegain replacement effect", "lifegain_replacement"),

    # Static or end-step checks against current life total rather than life gained this turn.
    # "if you have 30 or more life" (Serra Ascendant) or
    # "more than your starting life total" / "greater than your starting life total" (Angel of Destiny)
    (
        r"if you have \d+ or more life"
        r"|more than your starting life total"
        r"|greater than your starting life total",
        "Life total threshold",
        "lifegain_total",
    ),
]

# ── Producer map ──────────────────────────────────────────────────────────────

PRODUCER_MAP: dict[str, str] = {
    # Cards that gain life or grant lifelink
    "lifegain": LIFEGAIN_PRODUCER_SQL,

    # Lifegain threshold payoffs: direct lifegain sources PLUS Food token creators.
    # Consumers: Resplendent Angel, Angelic Accord, Valkyrie Harbinger.
    "lifegain_threshold": LIFEGAIN_THRESHOLD_PRODUCER_SQL,

    # Lifegain replacement effects: cards that modify how much life is gained.
    # Consumers: Angel of Vitality, Boon Reflection, Rhox Faithmender.
    "lifegain_replacement": LIFEGAIN_PRODUCER_SQL,

    # Life total threshold: cards that check current life total.
    # Consumers: Serra Ascendant ("if you have 30 or more life"), Angel of Destiny
    # ("more than your starting life total").
    "lifegain_total": LIFEGAIN_PRODUCER_SQL,
}
