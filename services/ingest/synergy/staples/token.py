"""Creature token generator SQL — cards that create creature tokens as a primary output.

Matches the "create ... creature token(s)" phrasing common to all token
generators, using a case-insensitive regex to handle quantity variants
(a single token, X tokens, "that many" tokens, numbered tokens).

Examples:
  single token     — Ophiomancer: "at the beginning of each upkeep, if you
                       control no Snakes, create a 1/1 black Snake creature
                       token with deathtouch"
  scaled token     — Krenko, Mob Boss: "tap: create X 1/1 red Goblin creature
                       tokens, where X is the number of Goblins you control"
  triggered token  — Grave Titan: "whenever Grave Titan enters the battlefield
                       or attacks, create two 2/2 black Zombie creature tokens"
  copy token       — Delina, Wild Mage: "whenever Delina attacks, choose target
                       creature you control … create a token that's a copy"

Intentionally excludes non-creature tokens (Treasure, Food, Clue, Blood) —
those are captured by their own staple modules.  The focus here is sacrifice
fodder: creature tokens can attack, block, and die to feed sac-outlet engines.

Used as CONSUMER SQL for sacrifice-payoff commanders (Korvold, Prossh, Meren)
alongside sac outlets and Treasure generators.

RATE is not defined — this module is not in STAPLE_CATEGORIES.  It is used
directly as a WHERE fragment in commander_mechanics.py.
"""

from __future__ import annotations

SQL: str = "oracle_text ~* 'create .{0,30}creature token'"
