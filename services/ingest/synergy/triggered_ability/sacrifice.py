import re

from regex_utils import p

# Cards that self-sacrifice at end of turn — reliable, scheduled death-trigger
# fodder.  These are CONSUMERS of a death-trigger commander (Syr Konrad, Teysa
# Karlov, Ayara): every token or creature here will fire the commander's trigger
# on a predictable schedule without requiring a sac outlet.
#
# Key breakdown:
#   sacrifice_eot    — explicit end-of-step self-sacrifice clause:
#                      "sacrifice it at the beginning of the next end step"
#                      (Zombie tokens produced by Feldon of the Third Path,
#                       Sedris of the Cabal's unearth targets, Young Necromancer,
#                       and any card that creates a temporary creature via
#                       unearth / encore / dash)
#                      Also matches the rules reminder text inside the decayed
#                      keyword reminder, so decayed-token creators are captured
#                      by both keys.
#   keyword_decayed  — the Decayed keyword itself (Innistrad: Midnight Hunt /
#                      Crimson Vow Zombie tokens):
#                      "decayed (This creature can't block. When it attacks,
#                       sacrifice it at the beginning of the next end step.)"
#                      Sources: Jadar, Ghoulcaller of Nephalia ("create a 2/2
#                        black Zombie creature token with decayed"); Champion of
#                        the Perished ("put a +1/+1 counter on ~ for each
#                        creature with decayed that died this turn")
#                      Decayed tokens are single-use attackers — they attack
#                      once and die, firing death triggers reliably.

PATTERNS: list[tuple[str, str, re.Pattern]] = [
    ("sacrifice_eot",    "Self-sacrifice at end of turn",
     p(r"sacrifice (?:it|this creature|this permanent|[a-z\s]{1,20})"
       r" at the beginning of (?:the |your )?(?:next )?end step")),
    ("keyword_decayed",  "Keyword: Decayed",
     p(r"\bdecayed\b")),
]
