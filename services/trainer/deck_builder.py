"""Phase 5 deckbuilding layer — deterministic post-processing with DB access.

Takes Phase 4's ranked card list and assembles a legal, playable 99-card
Commander deck (not including the commander card itself).

Design
------
Phase 4 scores cards by thematic fit with the commander.  It knows nothing
about infrastructure: Sol Ring, lands, or removal quotas.  This layer handles
all of that deterministically, using the DB to source role-appropriate cards
and quality-scored lands.

Assembly order
--------------
1. Forced ramp   — Sol Ring (CMC-1 slot), Arcane Signet (CMC-2 slot).
2. Forced lands  — Command Tower, Exotic Orchard, Terramorphic Expanse,
                   Evolving Wilds (consume from the 36-land budget).
3. Role fill     — ramp, removal, sweeper, draw_engine, draw_spell, interaction;
                   DB queries the staple SQL filtered by commander color identity,
                   then selects the highest Phase 4 ranked candidates per role.
4. Curve fill    — remaining spell slots filled by Phase 4 model score, bucketed
                   by CMC.
5. Land fill     — nonbasics (from DB manabase/utilityland SQL, up to 20 total),
                   then basics distributed by pip ratio from chosen spells.

Public API
----------
    result = build(
        ranked_cards   = [...],   # card IDs in Phase 4 score order
        commander_id   = "...",
        color_identity = frozenset({"G", "B"}),
        card_meta      = {...},   # from artifact["card_meta"]
        conn           = psycopg2_connection,
    )
    # result["deck"]      — list[str], exactly 99 card IDs (not including commander)
    # result["breakdown"] — dict with slot counts per role / curve / land type
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import TYPE_CHECKING

from mtg_sql.staples.ramp import SQL as _RAMP_SQL

if TYPE_CHECKING:
    import psycopg2

# ── Deck structure constants ──────────────────────────────────────────────────

LAND_TARGET   = 36
SPELL_SLOTS   = 99 - LAND_TARGET   # 63
NONBASIC_CAP  = 20   # max non-basic lands (including forced)

# Depending on the mana value of your commander, your desired curve may shift slightly.

CURVE_BUCKET_MV2: list[tuple[int, int]] = [
    (1,   9),
    (2,  17),
    (3,  17),
    (4,  12),
    (5,   5),
    (99,  2),
]

CURVE_BUCKET_MV3: list[tuple[int, int]] = [
    (1,   9),
    (2,  18),
    (3,  13),
    (4,  14),
    (5,   5),
    (99,  2),
]

CURVE_BUCKET_MV4: list[tuple[int, int]] = [
    (1,   6),
    (2,  20),
    (3,  19),
    (4,  10),
    (5,   4),
    (99,  2),
]

CURVE_BUCKET_MV5: list[tuple[int, int]] = [
    (1,   4),
    (2,  16),
    (3,  20),
    (4,  12),
    (5,   6),
    (99,  2),
]

CURVE_BUCKET_MV6: list[tuple[int, int]] = [
    (1,   2),
    (2,  14),
    (3,  20),
    (4,  15),
    (5,   5),
    (99,  3),
]

# assert sum(t for _, t in CURVE_BUCKET_MV2) == SPELL_SLOTS, CURVE_BUCKET_MV2
# assert sum(t for _, t in CURVE_BUCKET_MV3) == SPELL_SLOTS, CURVE_BUCKET_MV3
# assert sum(t for _, t in CURVE_BUCKET_MV4) == SPELL_SLOTS, CURVE_BUCKET_MV4
# assert sum(t for _, t in CURVE_BUCKET_MV5) == SPELL_SLOTS, CURVE_BUCKET_MV5
# assert sum(t for _, t in CURVE_BUCKET_MV6) == SPELL_SLOTS, CURVE_BUCKET_MV6


def _select_curve_buckets(commander_cmc: float | None) -> list[tuple[int, int]]:
    """Return the curve bucket list appropriate for the commander's mana value."""
    if commander_cmc is None or commander_cmc <= 2:
        return CURVE_BUCKET_MV2
    if commander_cmc <= 3:
        return CURVE_BUCKET_MV3
    if commander_cmc <= 4:
        return CURVE_BUCKET_MV4
    if commander_cmc <= 5:
        return CURVE_BUCKET_MV5
    return CURVE_BUCKET_MV6

# Forced inclusions — names exactly as they appear in MTGJSON
FORCED_RAMP: dict[str, int] = {
    "Sol Ring":      1,   # CMC 1 slot
    "Arcane Signet": 2,   # CMC 2 slot
}
FORCED_LANDS: tuple[str, ...] = (
    "Command Tower",
    "Exotic Orchard",
    "Terramorphic Expanse",
    "Evolving Wilds",
)

# Role fill targets — cards sourced from DB staple SQL
ROLE_TARGETS: dict[str, int] = {
    "ramp":        8,   # beyond Sol Ring + Arcane Signet → 10 total
    "removal":     6,
    "sweeper":     2,
    "draw_engine": 5,
    "draw_spell":  3,
    "interaction": 3,
}

COLOR_TO_BASIC: dict[str, str] = {
    "W": "Plains",
    "U": "Island",
    "B": "Swamp",
    "R": "Mountain",
    "G": "Forest",
    "C": "Wastes",
}

# ── Staple SQL (mirrors services/ingest/synergy/staples/*.py) ─────────────────
# Duplicated here to keep the trainer service self-contained; must stay in sync
# with the ingest SQL when patterns change.

_ROLE_SQL: dict[str, str] = {
    "ramp": _RAMP_SQL,
    "removal": (
        "("
        "  oracle_text ILIKE '%%destroy target%%'"
        "  OR oracle_text ILIKE '%%exile target%%'"
        "  OR"
        "  (oracle_text ILIKE '%%return target%%'"
        "   AND oracle_text ILIKE '%%owner%%s hand%%')"
        "  OR"
        "  oracle_text ~* 'gets? -[0-9]+/-[0-9]+ until end of turn'"
        ")"
        " AND type_line NOT ILIKE '%%Land%%'"
    ),
    "sweeper": (
        "("
        "  oracle_text ILIKE '%%destroy all%%'"
        "  OR oracle_text ILIKE '%%exile all%%'"
        "  OR oracle_text ~* 'deals? [0-9]+ damage to each creature'"
        "  OR oracle_text ~* 'each creature gets -[0-9]+/-[0-9]+'"
        "  OR oracle_text ILIKE '%%return all nonland%%'"
        ")"
        " AND type_line NOT ILIKE '%%Land%%'"
    ),
    "draw_engine": (
        "type_line NOT ILIKE '%%Instant%%'"
        " AND type_line NOT ILIKE '%%Sorcery%%'"
        " AND type_line NOT ILIKE '%%Land%%'"
        " AND oracle_text ILIKE '%%draw%%'"
        " AND oracle_text ILIKE '%%card%%'"
        " AND ("
        "   oracle_text ILIKE '%%whenever%%'"
        "   OR oracle_text ILIKE '%%at the beginning%%'"
        " )"
    ),
    "draw_spell": (
        "("
        "  type_line ILIKE '%%Instant%%'"
        "  OR type_line ILIKE '%%Sorcery%%'"
        ")"
        " AND oracle_text ILIKE '%%draw%%'"
        " AND oracle_text ILIKE '%%card%%'"
        " AND type_line NOT ILIKE '%%Land%%'"
    ),
    "interaction": (
        "("
        "  oracle_text ILIKE '%%counter target%%'"
        "  OR oracle_text ILIKE '%%gain hexproof%%'"
        "  OR oracle_text ILIKE '%%gains hexproof%%'"
        "  OR oracle_text ILIKE '%%have hexproof%%'"
        "  OR oracle_text ILIKE '%%has hexproof%%'"
        "  OR oracle_text ILIKE '%%gain indestructible%%'"
        "  OR oracle_text ILIKE '%%gains indestructible%%'"
        "  OR oracle_text ILIKE '%%have indestructible%%'"
        "  OR oracle_text ILIKE '%%has indestructible%%'"
        "  OR oracle_text ILIKE '%%shroud%%'"
        ")"
        " AND type_line NOT ILIKE '%%Land%%'"
    ),
}

# SQL for nonbasic land selection — ordered by quality tier within the query
_NONBASIC_LAND_SQL = (
    "type_line ILIKE '%%Land%%'"
    " AND type_line NOT ILIKE '%%Basic%%'"
    # exclude colorless utility lands with no color production
    # (a simple heuristic: if name is not a recognised land just include it)
)

# ── Pip counting ──────────────────────────────────────────────────────────────

_PIP_RE = re.compile(r"\{([WUBRG])\}")


def _count_pips(mana_cost: str) -> Counter:
    return Counter(_PIP_RE.findall(mana_cost))


# ── Internal helpers ──────────────────────────────────────────────────────────

def _name_to_id(name: str, card_meta: dict) -> str | None:
    name_lc = name.lower()
    for cid, meta in card_meta.items():
        if meta.get("name", "").lower() == name_lc:
            return cid
    return None


def _cmc_bucket(cmc: float | None, curve_buckets: list[tuple[int, int]]) -> int:
    if cmc is None:
        return 999
    for max_cmc, _ in curve_buckets:
        if cmc <= max_cmc:
            return max_cmc
    return 999


def _is_land(meta: dict) -> bool:
    return "Land" in meta.get("type_line", "")


def _is_basic(meta: dict) -> bool:
    tl = meta.get("type_line", "")
    return "Basic" in tl and "Land" in tl


def _query_card_ids(where_sql: str, color_identity: frozenset[str], conn) -> set[str]:
    """Run a staple WHERE query and return card IDs legal under the color identity."""
    query = (
        f"SELECT id::text, color_identity FROM cards WHERE ({where_sql})"
    )
    with conn.cursor() as cur:
        cur.execute(query)
        result: set[str] = set()
        for cid, ci in cur.fetchall():
            card_ci = frozenset(ci or [])
            if card_ci <= color_identity:
                result.add(cid)
    return result


def _query_nonbasic_lands(color_identity: frozenset[str], conn) -> list[str]:
    """Return all non-basic lands legal for the commander, ordered by quality tier."""
    # Tier ordering: higher is better
    # We can use type_line and name heuristics in SQL; land quality = how many
    # colored mana symbols the land can produce for this commander's identity.
    # Simplest: just return all legal non-basics; caller ranks by tier.
    query = (
        "SELECT id::text, color_identity FROM cards"
        f" WHERE ({_NONBASIC_LAND_SQL})"
    )
    with conn.cursor() as cur:
        cur.execute(query)
        result: list[str] = []
        for cid, ci in cur.fetchall():
            card_ci = frozenset(ci or [])
            if card_ci <= color_identity:
                result.append(cid)
    return result


# ── Public API ────────────────────────────────────────────────────────────────

def build(
    ranked_cards: list[str],
    commander_id: str,
    color_identity: frozenset[str],
    card_meta: dict[str, dict],
    conn,
) -> dict:
    """Assemble a 99-card Commander deck from Phase 4's ranked output.

    Parameters
    ----------
    ranked_cards:
        Card IDs in descending Phase 4 model-score order.  Must already be
        filtered to color-legal, commander-legal candidates.
    commander_id:
        The commander's card_id (excluded from the deck).
    color_identity:
        The commander's color identity as a frozenset of WUBRG strings.
    card_meta:
        {card_id: {name, mana_cost, type_line, cmc}} from the artifact.
    conn:
        A live psycopg2 connection.  Used for staple SQL and land queries.

    Returns
    -------
    dict with keys:
        "deck"      — list[str], exactly 99 card IDs (not including commander).
                      Basics appear multiple times (one entry per copy).
        "breakdown" — slot counts per role / curve / land type
    """
    # Select mana curve targets based on the commander's own mana value.
    commander_cmc: float | None = card_meta.get(commander_id, {}).get("cmc")
    curve_buckets = _select_curve_buckets(commander_cmc)

    # rank_index: card_id → position in Phase 4 ranking (lower = better scored)
    rank_index: dict[str, int] = {cid: i for i, cid in enumerate(ranked_cards)}

    chosen: set[str] = set()  # non-basic cards chosen (prevents duplicates)
    deck: list[str] = []

    breakdown: dict = {
        "forced_ramp":   [],
        "forced_lands":  [],
        "role":          defaultdict(list),
        "curve":         defaultdict(list),
        "nonbasic_land": [],
        "basic_land":    {},
    }

    def _resolve(name: str) -> str | None:
        cid = _name_to_id(name, card_meta)
        if cid and cid not in chosen and cid != commander_id:
            return cid
        return None

    def _pick(cid: str, slot: str, detail: str | None = None) -> None:
        deck.append(cid)
        chosen.add(cid)
        if detail is not None:
            breakdown[slot][detail].append(cid)  # type: ignore[index]
        else:
            breakdown[slot].append(cid)  # type: ignore[index,union-attr]

    # ── Step 1: Forced ramp ───────────────────────────────────────────────────
    for name in sorted(FORCED_RAMP, key=lambda n: FORCED_RAMP[n]):  # CMC order
        cid = _resolve(name)
        if cid:
            _pick(cid, "forced_ramp")

    # ── Step 2: Forced lands ──────────────────────────────────────────────────
    for name in FORCED_LANDS:
        cid = _resolve(name)
        if cid:
            _pick(cid, "forced_lands")

    # ── Step 3: Role filling (from DB staple SQL, ranked by Phase 4 score) ────
    for role, target in ROLE_TARGETS.items():
        where_sql = _ROLE_SQL[role]
        eligible = _query_card_ids(where_sql, color_identity, conn)
        # Sort eligible cards by Phase 4 rank (ascending = better scored)
        eligible_ranked = sorted(
            (cid for cid in eligible if cid not in chosen and cid != commander_id),
            key=lambda c: rank_index.get(c, len(ranked_cards)),
        )
        picked = 0
        for cid in eligible_ranked:
            if picked >= target:
                break
            meta = card_meta.get(cid, {})
            if _is_land(meta):
                continue  # staple SQL can match land-creatures; skip lands
            _pick(cid, "role", role)
            picked += 1

    # ── Step 4: Mana curve fill (remaining spell slots) ──────────────────────
    bucket_filled: dict[int, int] = defaultdict(int)
    for cid in (breakdown["forced_ramp"] + [c for cs in breakdown["role"].values() for c in cs]):
        meta = card_meta.get(cid, {})
        if not _is_land(meta):
            b = _cmc_bucket(meta.get("cmc"), curve_buckets)
            bucket_filled[b] += 1

    bucket_cap: dict[int, int] = {
        max_cmc: (target - bucket_filled.get(max_cmc, 0))
        for max_cmc, target in curve_buckets
        if (target - bucket_filled.get(max_cmc, 0)) > 0
    }

    for cid in ranked_cards:
        if not bucket_cap:
            break
        if cid in chosen:
            continue
        meta = card_meta.get(cid, {})
        if _is_land(meta):
            continue
        b = _cmc_bucket(meta.get("cmc"), curve_buckets)
        if b in bucket_cap:
            _pick(cid, "curve", str(b))
            bucket_cap[b] -= 1
            if bucket_cap[b] == 0:
                del bucket_cap[b]

    # ── Step 5a: Nonbasic land selection (from DB) ────────────────────────────
    nonbasic_budget = NONBASIC_CAP - len(breakdown["forced_lands"])
    if nonbasic_budget > 0:
        all_nonbasics = _query_nonbasic_lands(color_identity, conn)
        # Sort by Phase 4 rank (best model-scored lands first)
        all_nonbasics_ranked = sorted(
            (cid for cid in all_nonbasics if cid not in chosen and cid != commander_id),
            key=lambda c: rank_index.get(c, len(ranked_cards)),
        )
        for cid in all_nonbasics_ranked:
            if nonbasic_budget <= 0:
                break
            _pick(cid, "nonbasic_land")
            nonbasic_budget -= 1

    # ── Step 5b: Basic land fill ──────────────────────────────────────────────
    lands_placed = len(breakdown["forced_lands"]) + len(breakdown["nonbasic_land"])
    basics_needed = LAND_TARGET - lands_placed

    if basics_needed > 0:
        pip_total: Counter = Counter()
        for cid in chosen:
            meta = card_meta.get(cid, {})
            if not _is_land(meta):
                pip_total.update(_count_pips(meta.get("mana_cost", "")))

        active_colors = [c for c in color_identity if c in COLOR_TO_BASIC]
        if active_colors:
            total_pips = sum(pip_total[c] for c in active_colors)
            if total_pips == 0:
                per_color = {c: basics_needed // len(active_colors) for c in active_colors}
                leftover = basics_needed - sum(per_color.values())
                for i, c in enumerate(active_colors):
                    if i < leftover:
                        per_color[c] += 1
            else:
                per_color: dict[str, int] = {}
                allocated = 0
                for c in active_colors:
                    per_color[c] = round(pip_total[c] / total_pips * basics_needed)
                    allocated += per_color[c]
                diff = basics_needed - allocated
                for c in sorted(active_colors, key=lambda x: -pip_total[x]):
                    if diff == 0:
                        break
                    per_color[c] += 1 if diff > 0 else -1
                    diff += -1 if diff > 0 else 1
        else:
            per_color = {"C": basics_needed}

        # Look up basic land card IDs from DB by land subtype.
        # MTGJSON AtomicCards stores basics as "Forest // Forest" etc., so
        # name lookup by "Forest" fails.  Match by type_line subtype instead:
        # "Basic Land — Forest" → subtype "Forest" → color G.
        subtype_to_color = {v: k for k, v in COLOR_TO_BASIC.items()}  # e.g. "Forest" → "G"
        subtypes_needed = {
            COLOR_TO_BASIC[c] for c in per_color if c in COLOR_TO_BASIC and per_color[c] > 0
        }
        basic_color_to_id: dict[str, str] = {}  # color letter → card_id
        for subtype in subtypes_needed:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id::text FROM cards"
                    " WHERE type_line ILIKE %s"
                    " LIMIT 1",
                    (f"Basic Land — {subtype}",)
                )
                row = cur.fetchone()
                if row:
                    basic_color_to_id[subtype_to_color[subtype]] = row[0]

        for color, count in per_color.items():
            if count <= 0:
                continue
            basic_cid = basic_color_to_id.get(color)
            if not basic_cid:
                continue
            breakdown["basic_land"][color] = count
            for _ in range(count):
                deck.append(basic_cid)   # basics may repeat; NOT added to chosen

    # ── Sanity check & return ─────────────────────────────────────────────────
    total = len(deck)
    assert total == 99, (
        f"deck_builder produced {total} cards (expected 99); "
        f"forced_ramp={len(breakdown['forced_ramp'])}, "
        f"forced_lands={len(breakdown['forced_lands'])}, "
        f"role={sum(len(v) for v in breakdown['role'].values())}, "
        f"curve={sum(len(v) for v in breakdown['curve'].values())}, "
        f"nonbasic={len(breakdown['nonbasic_land'])}, "
        f"basics={sum(breakdown['basic_land'].values())}"
    )

    return {"deck": deck, "breakdown": breakdown}
