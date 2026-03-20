"""Commander-value synergy patterns and producer SQL fragments.

"Commander-value" cards are non-commander cards whose rules text explicitly
confers a benefit *because* you control your commander — either by:

  1. Reducing (or eliminating) the card's casting cost, e.g.
       "If you control a commander, you may cast this spell without paying
        its mana cost." (Deflecting Swat, Fierce Guardianship, …)
  2. Gaining additional modes or effects when a commander is present, e.g.
       "As long as you control a commander, ~ has [ability]." (Loyal Apprentice)
  3. Producing mana equal to a commander's stats / color identity, e.g.
       Jeska's Will ("add RRR if you control a commander"), Mox Amber (adds mana
       matching the legend it's attached to).

These cards reward having a commander that is *frequently in play* — which
low mana-value (CMC 0–2) commanders enable by being cheap to cast initially
and cheap to recast from the command zone.

Trigger events defined here
----------------------------
``commander_free_cast``
    Payoff: the card can be cast for free (or at reduced cost) while you
    control your commander.  Consumer SQL: oracle text contains
    "if you control a commander" near a cost-reduction phrase.

``commander_in_play_payoff``
    Payoff: the card gains a meaningful bonus (ability, mode, stat boost)
    while you control your commander.  Consumer SQL: oracle text checks for
    a commander's presence outside a mana-cost context.

``commander_mana_value``
    Payoff: the card produces mana based on the commander's mana value, power,
    toughness, or other characteristic.  Producers: legendary permanent cards
    that function as commanders (the card whose stat is referenced).  Consumers:
    cards like Jeska's Will, Mox Amber, Selvala, or Sisay.

The producer for ``commander_free_cast`` and ``commander_in_play_payoff`` is
any legendary creature or planeswalker card with CMC ≤ 2 (i.e. a "low-MV"
commander).  The synergy direction is:

    producer (low-MV commander) → consumer (commander-value card)

meaning: "this commander makes that support card better".

The producer for ``commander_mana_value`` is any legendary permanent (creature
or planeswalker) that could plausibly be a commander.

Score tiers
-----------
* ``commander_free_cast`` edges → score 1.0 (hard payoff: literally free spell)
* ``commander_in_play_payoff`` edges → score 0.8 (strong payoff: persistent bonus)
* ``commander_mana_value`` edges → score 0.6 (softer payoff: stat-dependent mana)

All edges use ``score_type = 'commander_value'`` so Phase 2 training and the
fallback stub can query them independently of ``ability_trigger`` edges.
"""

from __future__ import annotations

# ── Trigger / consumer patterns ───────────────────────────────────────────────
# Used by pipeline.tag_abilities() (Pass 1) to tag *consumer* cards with a
# trigger_event that the PRODUCER_MAP SQL can then cross-join against.
#
# Format: (regex_pattern, ability_name, trigger_event)

TRIGGER_PATTERNS: list[tuple[str, str, str]] = [
    # Free / cost-reduced cast while controlling a commander
    # Covers: "If you control a commander, you may cast this spell without
    #          paying its mana cost."  (Deflecting Swat, Fierce Guardianship,
    #          Flawless Maneuver, Deadly Rollick, Obscuring Haze, Mandible
    #          Justiciar, Fierce Guardianship, etc.)
    # Also: "this spell costs {X} less if you control a commander" variants.
    (
        r"if you control a commander.{0,80}"
        r"(without paying (its|the) mana cost"
        r"|costs?.{0,20}less"
        r"|free)",
        "Commander free-cast payoff",
        "commander_free_cast",
    ),

    # Persistent in-play bonus while controlling a commander
    # Covers:
    #   "As long as you control a commander, ~" (Loyal Apprentice, Loran's Escape)
    #   "~ has [keyword] as long as you control a commander."
    #   "If you control a commander, add {R}{R}{R}." (Jeska's Will first mode)
    #   "If you control a commander, exile the top X cards …" (Jeska's Will second mode)
    #   Any other "if/as long as/while you control a commander, [do something]" clause.
    # The second alternative uses a broad match: any non-period text up to 120 chars
    # after the trigger phrase is treated as a benefit.  The `commander_free_cast`
    # pattern takes priority for free-cast wordings (mana cost reduction phrases)
    # because TRIGGER_PATTERNS is matched in order and free-cast is listed first.
    (
        r"(as long as|while) you control a commander"
        r"|if you control a commander[,\s].{0,120}",
        "Commander in-play payoff",
        "commander_in_play_payoff",
    ),

    # Mana production scaled by commander's mana value or characteristics
    # Covers: Mox Amber ("add one mana of any type that a legendary creature
    #          or planeswalker you control could produce"), Selvala Heart of
    #          the Wilds ("Add X mana … where X is the greatest power among
    #          creatures you control"), Sisay Weatherlight Captain (tutors
    #          legendaries by legendary count).
    # We target the *generic* "legendary creature or planeswalker … mana"
    # wording rather than the specific card text to stay extensible.
    (
        r"legendary (creature|planeswalker).{0,60}(mana|add)"
        r"|add.{0,30}legendary (creature|planeswalker)",
        "Commander mana-value / legendary mana payoff",
        "commander_mana_value",
    ),
]

# ── Producer map ──────────────────────────────────────────────────────────────
# Maps each trigger_event → SQL WHERE fragment that identifies *producer* cards.
#
# ``commander_free_cast`` and ``commander_in_play_payoff``:
#   Producer = low-MV legendary creature or planeswalker (CMC ≤ 2).
#   Rationale: the value of these support cards scales directly with how often
#   the commander is in play.  A CMC-0 or CMC-1 commander (Rograkh, Yoshimaru,
#   Isamaru) is almost always available; a CMC-2 commander (Thrasios, Kraum,
#   Lurrus) recasts cheaply after removal.  CMC-3+ commanders spend meaningful
#   resources on the command-zone tax, so the synergy is weaker.
#
# ``commander_mana_value``:
#   Producer = any legendary creature or planeswalker (no CMC cap), because
#   Mox Amber and similar cards work with any legend in play.

PRODUCER_MAP: dict[str, str] = {
    # Low-MV commanders: CMC ≤ 2, legendary, creature or planeswalker
    "commander_free_cast": (
        "(lower(type_line) LIKE '%legendary%')"
        " AND ("
        "    lower(type_line) LIKE '%creature%'"
        "    OR lower(type_line) LIKE '%planeswalker%'"
        " )"
        " AND cmc <= 2"
        " AND (legalities->>'commander' = 'legal'"
        "      OR legalities->>'commander' IS NULL)"
    ),

    "commander_in_play_payoff": (
        "(lower(type_line) LIKE '%legendary%')"
        " AND ("
        "    lower(type_line) LIKE '%creature%'"
        "    OR lower(type_line) LIKE '%planeswalker%'"
        " )"
        " AND cmc <= 2"
        " AND (legalities->>'commander' = 'legal'"
        "      OR legalities->>'commander' IS NULL)"
    ),

    # Any legendary creature or planeswalker — the "legend matters" mana cards
    "commander_mana_value": (
        "(lower(type_line) LIKE '%legendary%')"
        " AND ("
        "    lower(type_line) LIKE '%creature%'"
        "    OR lower(type_line) LIKE '%planeswalker%'"
        " )"
        " AND (legalities->>'commander' = 'legal'"
        "      OR legalities->>'commander' IS NULL)"
    ),
}

# Score to store per trigger_event for commander_value edges.
# Higher score = stronger / more direct payoff.
EDGE_SCORES: dict[str, float] = {
    "commander_free_cast":      1.0,
    "commander_in_play_payoff": 0.8,
    "commander_mana_value":     0.6,
}
