"""Deck analysis utilities: archetype and win-condition detection.

Shared between import_moxfield.py and import_decklists.py (both in the ingest
service).  The API service (services/api/ops/import_utils.py) contains an
identical copy of ``detect_archetype()`` because the two containers do not
share a Python path.

Usage
-----
    from import_utils import detect_archetype, fetch_card_details

    card_details = await fetch_card_details(conn, card_ids)
    arch_meta = detect_archetype(card_details)
    # arch_meta keys: archetype, win_conditions, avg_cmc, role_counts
"""

from __future__ import annotations

import re
from typing import Any

# ── Re-use compiled role patterns from synergy/roles.py ──────────────────────
# Group them by role name so we can count how many deck cards match each role.
from synergy.roles import ROLE_PATTERNS, LAND_ROLE_PATTERNS, is_land_card

_COMPILED_BY_ROLE: dict[str, list[re.Pattern]] = {}
for _pat, _role, *_ in ROLE_PATTERNS + LAND_ROLE_PATTERNS:
    _COMPILED_BY_ROLE.setdefault(_role, []).append(
        re.compile(_pat, re.IGNORECASE)
    )


def _card_has_role(oracle_text: str, type_line: str, role: str) -> bool:
    """Return True if the card's oracle text matches any pattern for *role*."""
    text = oracle_text or ""
    for pat in _COMPILED_BY_ROLE.get(role, []):
        if pat.search(text):
            return True
    return False


# ── Archetype-specific patterns ───────────────────────────────────────────────

# Stax: effects that restrict or tax opponents
_STAX_RES: list[re.Pattern] = [
    re.compile(r"opponents? can't", re.IGNORECASE),
    re.compile(r"skip (your|their) (untap|upkeep|draw)", re.IGNORECASE),
    re.compile(r"pay \{[0-9X]+\} more to (cast|play)", re.IGNORECASE),
    re.compile(r"spells? costs? \{[0-9X]+\} more", re.IGNORECASE),
    re.compile(r"unless (its controller|they|that player) pays? \{", re.IGNORECASE),
    re.compile(r"each (player|opponent) (can't|doesn't untap|skips)", re.IGNORECASE),
    re.compile(r"players? can't (cast|play|untap|draw)", re.IGNORECASE),
]

# Punisher / group slug: hurt opponents for their normal actions or passively
_PUNISHER_RES: list[re.Pattern] = [
    re.compile(
        r"whenever (an opponent|each opponent).{0,80}"
        r"(loses? \d+ life|takes? \d+ damage)",
        re.IGNORECASE,
    ),
    re.compile(r"each opponent loses \d+ life", re.IGNORECASE),
    re.compile(r"each player loses \d+ life", re.IGNORECASE),
    re.compile(
        r"whenever (a player|an opponent) "
        r"(casts? a spell|draws? (a card|\d+ cards?)|gains? life|plays? a land)"
        r".{0,80}(loses? \d+ life|takes? \d+ damage)",
        re.IGNORECASE,
    ),
    re.compile(
        r"at the beginning of (each|your) (opponent's|upkeep).{0,80}loses? \d+ life",
        re.IGNORECASE,
    ),
]

# Spellslinger payoffs: magecraft, storm, prowess, triggered on instant/sorcery
_SPELLSLINGER_RE = re.compile(
    r"\b(magecraft|storm|prowess|whenever you cast (an instant|a sorcery|a spell))\b",
    re.IGNORECASE,
)

# Win-condition sub-types
_WIN_CON_RES: dict[str, re.Pattern] = {
    "infect": re.compile(r"\b(infect|toxic \d)\b", re.IGNORECASE),
    "aristocrats": re.compile(
        r"(whenever (a|another) creature (dies|is put into (a|your) graveyard)"
        r".{0,80}(you gain|you lose|you draw|deal \d+ damage))"
        r"|(whenever you sacrifice a creature.{0,80}"
        r"(you gain|you lose|you draw|deal \d+ damage))",
        re.IGNORECASE | re.DOTALL,
    ),
    "group_slug": re.compile(
        r"(each opponent loses \d+ life"
        r"|deals? \d+ damage to each (opponent|player)"
        r"|each (opponent|player) loses \d+ life)",
        re.IGNORECASE,
    ),
    "lifegain": re.compile(
        r"whenever you gain (life|\d+ or more life)",
        re.IGNORECASE,
    ),
    "storm": re.compile(r"\bstorm\b", re.IGNORECASE),
}


# ── Public API ────────────────────────────────────────────────────────────────

def detect_archetype(cards: list[dict]) -> dict[str, Any]:
    """Detect deck archetype from card composition.

    Args:
        cards: list of dicts with keys ``oracle_text``, ``type_line``,
               ``cmc``, and optionally ``keywords``.  All values may be
               ``None``; missing keys are treated as empty / zero.

    Returns:
        A dict with:

        ``archetype``
            Primary archetype label: one of ``aggro``, ``combo``,
            ``control``, ``midrange``, ``punisher``, ``reanimator``,
            ``spellslinger``, ``stax``, ``tokens``, or ``unknown``.

        ``win_conditions``
            List of detected win-condition sub-types such as ``infect``,
            ``aristocrats``, ``group_slug``, ``lifegain``, ``storm``.

        ``avg_cmc``
            Average mana value of non-land cards (rounded to 2 dp).

        ``role_counts``
            Dict of ``{ramp, draw, removal, tutor}`` counts.
    """
    if not cards:
        return {
            "archetype":      "unknown",
            "win_conditions": [],
            "avg_cmc":        0.0,
            "role_counts":    {"ramp": 0, "draw": 0, "removal": 0, "tutor": 0},
        }

    # ── Basic card-type counts ────────────────────────────────────────────────
    creature_count       = 0
    instant_sorcery_count = 0
    non_land_cmcs: list[float] = []

    for card in cards:
        tl  = (card.get("type_line") or "").lower()
        cmc = float(card.get("cmc") or 0)
        if "creature" in tl:
            creature_count += 1
        if "instant" in tl or "sorcery" in tl:
            instant_sorcery_count += 1
        if "land" not in tl:
            non_land_cmcs.append(cmc)

    avg_cmc = round(sum(non_land_cmcs) / len(non_land_cmcs), 2) if non_land_cmcs else 0.0

    # ── Role counts (reuse synergy/roles.py patterns) ─────────────────────────
    def _count_role(role: str) -> int:
        return sum(
            1 for c in cards
            if _card_has_role(c.get("oracle_text") or "", c.get("type_line") or "", role)
        )

    ramp_count        = _count_role("ramp")
    draw_count        = _count_role("draw_one") + _count_role("repeatable_draw")
    removal_count     = _count_role("removal") + _count_role("sweeper")
    tutor_count       = _count_role("tutor")
    token_count       = _count_role("token_generator")
    recursion_count   = _count_role("recursion")
    anthem_count      = _count_role("anthem")
    interaction_count = _count_role("interaction")
    win_cond_count    = _count_role("win_condition")

    role_counts = {
        "ramp":    ramp_count,
        "draw":    draw_count,
        "removal": removal_count,
        "tutor":   tutor_count,
    }

    # ── Archetype-specific pattern counts ─────────────────────────────────────
    def _count_patterns(patterns: list[re.Pattern]) -> int:
        return sum(
            1 for c in cards
            if any(p.search(c.get("oracle_text") or "") for p in patterns)
        )

    stax_count          = _count_patterns(_STAX_RES)
    punisher_count      = _count_patterns(_PUNISHER_RES)
    spellslinger_payoffs = sum(
        1 for c in cards
        if _SPELLSLINGER_RE.search(c.get("oracle_text") or "")
    )

    # ── Win-condition sub-type detection ──────────────────────────────────────
    win_conditions: list[str] = []
    for wc_name, wc_re in _WIN_CON_RES.items():
        if any(wc_re.search(c.get("oracle_text") or "") for c in cards):
            win_conditions.append(wc_name)

    # Also check keywords list for infect/toxic (not always in oracle text)
    if "infect" not in win_conditions:
        for c in cards:
            kws = c.get("keywords") or []
            if isinstance(kws, list) and any(
                k.lower() in ("infect", "toxic") for k in kws
            ):
                win_conditions.append("infect")
                break

    # ── Archetype classification (priority order) ─────────────────────────────
    # Evaluate the most distinctive archetypes first so that a deck with a
    # strong primary identity is not mis-labelled by a secondary signal.
    if stax_count >= 5:
        archetype = "stax"
    elif punisher_count >= 5:
        archetype = "punisher"
    elif recursion_count >= 5:
        archetype = "reanimator"
    elif token_count >= 8:
        archetype = "tokens"
    elif tutor_count >= 3 and (win_cond_count >= 2 or len(win_conditions) >= 1):
        archetype = "combo"
    elif instant_sorcery_count >= 10 and spellslinger_payoffs >= 2:
        archetype = "spellslinger"
    elif removal_count >= 18 and interaction_count >= 8:
        archetype = "control"
    elif creature_count >= 35 and avg_cmc <= 2.8 and anthem_count >= 3:
        archetype = "aggro"
    elif draw_count >= 10:
        archetype = "midrange"
    else:
        archetype = "midrange"  # sensible default: incremental value / good-stuff

    return {
        "archetype":      archetype,
        "win_conditions": win_conditions,
        "avg_cmc":        avg_cmc,
        "role_counts":    role_counts,
    }


# ── DB fetch helper (asyncpg) ─────────────────────────────────────────────────

async def fetch_card_details(conn, card_ids: list[str]) -> list[dict]:
    """Return card detail rows needed for archetype detection.

    Uses an asyncpg connection (the low-level driver used by the ingest
    scripts).  Returns a list of dicts with keys ``oracle_text``,
    ``type_line``, ``cmc``, and ``keywords``.
    """
    if not card_ids:
        return []
    rows = await conn.fetch(
        """
        SELECT id::text, oracle_text, type_line, cmc, keywords
        FROM cards
        WHERE id::text = ANY($1)
        """,
        card_ids,
    )
    return [dict(row) for row in rows]
