"""Treasure generator SQL — cards that create Treasure tokens as a primary output.

Captures the full Treasure-generator population via a single oracle-text
pattern: any card whose rules text contains "Treasure token" is producing
(or at minimum interacting with) Treasures.

Examples:
  create_treasure  — "create a Treasure token" / "create X Treasure tokens"
                     Dockside Extortionist: "create a Treasure token for each
                       artifact and enchantment your opponents control"
                     Smothering Tithe: "that player doesn't pay, you create a
                       Treasure token"
                     Captain Lannery Storm: "whenever Captain Lannery Storm
                       attacks, create a Treasure token"
                     Pitiless Plunderer: "whenever another creature you control
                       dies, create a Treasure token"
                     Goldspan Dragon: "whenever Goldspan Dragon attacks or
                       becomes the target of a spell, create a Treasure token"
                     Revel in Riches: "whenever a creature an opponent controls
                       dies, create a Treasure token"

Used as CONSUMER SQL for sacrifice-payoff commanders (Korvold, Prossh, Meren)
who need sacrifice fodder: Treasure tokens are ideal because they also produce
mana when sacrificed, giving double value.

RATE is not defined — this module is not in STAPLE_CATEGORIES.  It is used
directly as a WHERE fragment in commander_mechanics.py.
"""

from __future__ import annotations

SQL: str = "oracle_text ILIKE '%% Treasure token%%'"
