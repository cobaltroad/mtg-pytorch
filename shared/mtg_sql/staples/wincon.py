"""Win-condition staple SQL — cards that end the game on purpose.

Consumed by the composition builder's wincon audit (#141): a deck can pass
every structural check and still have no way to win, so the builder
guarantees at least WINCON_MIN deliberate finishers.

Deliberately tighter than services/api/ops/card_roles.py's win_condition
role (which counts any trample/double-strike card): membership here means
the card is a *finisher*, not merely combat-relevant.

Mode breakdown
--------------
ALT_WIN     — states a win/loss outright.  (Thassa's Oracle, Approach of
              the Second Sun, Revel in Riches, Felidar Sovereign)

X_SCALER    — X-scaling damage/drain aimed at players.  (Torment of
              Hailfire, Exsanguinate, Comet Storm, Crackle with Power)

OVERRUN     — one-shot team pump that turns a board into lethal.
              (Craterhoof Behemoth, Overrun, Triumph of the Hordes,
              End-Raze Forerunners)

EXTRA_COMBAT — additional combat phases multiply an attacking board.
              (Aggravated Assault, Moraug, Combat Celebrant)

Color identity filtering (card_ci ⊆ commander_ci) is applied by the caller.

RATE reflects the share of a 99-card deck devoted to deliberate finishers.
"""

from __future__ import annotations

RATE: float = 0.03

_NOT_LAND = "type_line NOT ILIKE '%%Land%%'"

ALT_WIN: str = (
    "("
    "  oracle_text ~* 'you win the game'"
    "  OR oracle_text ~* 'each opponent loses the game'"
    ")"
    f" AND {_NOT_LAND}"
)

X_SCALER: str = (
    "("
    # "deals X damage" / "deals five times X damage" (Crackle with Power).
    # 'each opponent/player/of' but NOT 'each creature' — X-sweepers
    # (Chain Reaction) kill boards, not players.
    "  oracle_text ~* 'deals? ([a-z]+ times )?x damage to (any target|each (opponent|player|of)|target (player|opponent)|up to)'"
    "  OR oracle_text ~* 'each opponent loses x life'"
    "  OR oracle_text ~* 'target (player|opponent) loses x life'"
    # repeat-X drains (Torment of Hailfire)
    "  OR oracle_text ~* 'repeat the following process x times[^.]*\\.[^.]*(loses \\d+ life|deals \\d+ damage)'"
    ")"
    f" AND {_NOT_LAND}"
)

OVERRUN: str = (
    # one-shot mass pump with an evasion/lethality rider, Overrun-class —
    # both templating orders: "get +X/+X … and gain trample" (Overrun) and
    # "gain trample and get +X/+X" (Craterhoof)
    "("
    "  oracle_text ~* 'creatures you control get \\+[0-9x]+/\\+[0-9x]+"
    "( and gain| and have)? .{0,40}(trample|double strike|indestructible)'"
    "  OR oracle_text ~* 'creatures you control gain (trample|double strike)"
    " and get \\+[0-9x]+/\\+[0-9x]+'"
    ")"
    f" AND {_NOT_LAND}"
)

EXTRA_COMBAT: str = (
    "oracle_text ~* '(additional combat phase|untap all creatures .{0,40}additional combat)'"
    f" AND {_NOT_LAND}"
)

SQL: str = f"({ALT_WIN} OR {X_SCALER} OR {OVERRUN} OR {EXTRA_COMBAT})"

#: Incremental drain/ping sources — Blood Artist, Zulaport Cutthroat,
#: Impact Tremors, Purphoros, Prodigal Sorcerer-class pingers.  A single
#: one is not a finisher, so this is deliberately NOT part of SQL above:
#: the builder's win-path audit counts their *density* (DRAIN_MIN in
#: shared/composition/builder.py) — enough of them is an attrition win
#: with no dedicated finisher at all.
DRAIN: str = (
    "("
    "  oracle_text ~* 'whenever[^.]{0,80}, (each opponent|that player|target (player|opponent)) loses \\d+ life'"
    "  OR oracle_text ~* 'whenever[^.]{0,80}deals? \\d+ damage to (each opponent|that player|any target)'"
    "  OR oracle_text ~* '\\{t\\}[^:]{0,30}: [^.]{0,20}deals? 1 damage to any target'"
    ")"
    f" AND {_NOT_LAND}"
)
