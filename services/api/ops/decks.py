"""Deck generation operations — wires up DeckConstructor model inference."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Compiled once at import time — used in boost logic inside generate().
# Matches mana-producing activated abilities like "{T}: Add {G}" or "Add {C}".
_MANA_ADD_RE = re.compile(r"\{[tT]\}\s*:\s*[Aa]dd|\badd \{", re.I)
# Score multiplier applied to mana-producing cards when "mana_producers" boost is active.
_MANA_PRODUCER_BOOST = 1.35

# ── Land budget constants ─────────────────────────────────────────────────────
# Hypergeometric: with 36 lands in 99 cards, E[lands in opening 7] ≈ 2.55
LAND_TARGET = 36
SPELL_SLOTS = 99 - LAND_TARGET       # 63 model-scored non-land cards
NONBASIC_LAND_CAP = 20               # max non-basic lands drawn from model scoring

# Auto-includes: present in virtually every Commander deck regardless of model score.
# Exclusion requires a strong contrary signal (future work).
GUARANTEED_NONBASICS = ("Command Tower", "Exotic Orchard")

COLOR_TO_BASIC = {
    "W": "Plains",
    "U": "Island",
    "B": "Swamp",
    "R": "Mountain",
    "G": "Forest",
    "C": "Wastes",   # colorless commanders
}


# ── Ramp budget ──────────────────────────────────────────────────────────────
RAMP_TARGET = 10                              # guaranteed non-land mana sources
GUARANTEED_RAMP = ("Sol Ring", "Arcane Signet")  # always include when available

# ── Mana curve targets ────────────────────────────────────────────────────────
# Each entry is (cmc_upper_bound_inclusive, target_slot_count).
# Bounds are checked in order; the last bucket is a catch-all for CMC 6+.
# Total must equal SPELL_SLOTS (63).
CURVE_BUCKETS: list[tuple[int, int]] = [
    (1,   8),   # CMC 0–1  — ramp, interaction, cheap rocks
    (2,  16),   # CMC 2    — two-drops, signets, removal
    (3,  14),   # CMC 3    — three-drops, value engines
    (4,  12),   # CMC 4    — midrange threats, board wipes
    (5,   7),   # CMC 5    — big threats
    (999, 6),   # CMC 6+   — finishers / haymakers
]
assert sum(t for _, t in CURVE_BUCKETS) == SPELL_SLOTS, "CURVE_BUCKETS must sum to SPELL_SLOTS"


TRIBAL_BOOST = 1.5   # score multiplier for cards belonging to the commander's referenced tribe(s)

# Irregular plural → singular mappings for MTG creature types
_PLURAL_IRREGULARS: dict[str, str] = {
    "elves": "elf",
    "wolves": "wolf",
}


def _curve_bucket(cmc: float) -> int:
    """Return the CURVE_BUCKETS index for a given CMC value."""
    for i, (cap, _) in enumerate(CURVE_BUCKETS):
        if cmc <= cap:
            return i
    return len(CURVE_BUCKETS) - 1


def _card_subtypes(type_line: str) -> frozenset[str]:
    """Return the creature subtypes of a card (text after the em dash in type_line)."""
    if "\u2014" in type_line:
        return frozenset(type_line.split("\u2014", 1)[1].split())
    return frozenset()


def _commander_tribal_types(oracle_text: str, known_subtypes: frozenset[str]) -> frozenset[str]:
    """Return creature type names that the commander's oracle text explicitly references.

    Reads the commander's oracle text (e.g. "tap ten untapped Elves you control")
    and returns the canonical singular subtype(s) mentioned.  Only returns types that
    actually exist in the card pool so we don't match arbitrary capitalised words.

    Examples:
      "Elves you control" + known={"Elf", ...} → frozenset({"Elf"})
      "create 1/1 Elf Warrior tokens" + known   → frozenset({"Elf", "Warrior"})
    """
    known_lower: dict[str, str] = {s.lower(): s for s in known_subtypes}
    found: set[str] = set()
    for word in re.findall(r"\b[A-Z][a-z]+\b", oracle_text or ""):
        lower = word.lower()
        # Direct match (singular form already in pool)
        if lower in known_lower:
            found.add(known_lower[lower])
            continue
        # Try irregular plural → singular
        singular = _PLURAL_IRREGULARS.get(lower)
        if singular and singular in known_lower:
            found.add(known_lower[singular])
            continue
        # Generic plural: strip trailing 's'
        if lower.endswith("s") and lower[:-1] in known_lower:
            found.add(known_lower[lower[:-1]])
    return frozenset(found)


def _count_pips(mana_cost: str | None) -> dict[str, int]:
    """Count color pips in a mana cost string like {2}{W}{W}{B}."""
    pips: dict[str, int] = {}
    for ch in re.findall(r"\{([WUBRG])\}", mana_cost or ""):
        pips[ch] = pips.get(ch, 0) + 1
    return pips


def _sync_db_url(db_url: str) -> str:
    return db_url.replace("postgresql+asyncpg://", "postgresql://")


async def generate(
    db: AsyncSession,
    commander_oracle_id: UUID,
    checkpoint: str,
    boost_overrides: list[str] | None = None,
) -> dict | None:
    """Generate a 99-card deck using the DeckConstructor model.

    Falls back to the synergy-based stub if the model or checkpoint is
    unavailable (logs a warning in that case).

    Parameters
    ----------
    boost_overrides:
        Optional list of boost keys derived from ``/commanders/{id}/analyze``
        (e.g. ``["mana_producers", "tribal"]``).  When ``"mana_producers"``
        is present, cards whose oracle text contains a mana-producing activated
        ability (``{T}: Add``) receive a 1.35× score multiplier so the model
        surfaces mana-dorks even when training signal is sparse.
    """
    # Resolve commander card
    result = await db.execute(
        text("""
            SELECT id, oracle_id, name, type_line, oracle_text, color_identity, mana_cost, cmc
            FROM cards WHERE oracle_id = :oid
        """),
        {"oid": str(commander_oracle_id)},
    )
    commander_row = result.fetchone()
    if not commander_row:
        return None

    commander_id = str(commander_row[0])
    color_identity = commander_row[5] or []

    db_url = DATABASE_URL

    # ── Attempt real model inference ─────────────────────────────────────────
    try:
        from ops import inference
        from ops.card_roles import detect_roles
        from ops.import_utils import detect_archetype

        loop = asyncio.get_event_loop()

        # Load model (lazy, cached)
        ckpt_name = checkpoint if checkpoint != "latest" else "phase4_best"
        model = await loop.run_in_executor(None, inference.get_model, ckpt_name)

        if model is not None:
            # Load embeddings (lazy, cached)
            embeddings = await loop.run_in_executor(None, inference.get_embeddings, db_url)

            if embeddings and commander_id in embeddings:
                # Get common context from existing decklists
                context_ids = await loop.run_in_executor(
                    None, inference.get_common_context, commander_id, db_url
                )

                all_ids = list(embeddings.keys())

                # Load color identities (lazy, cached) and filter to legal pool
                color_identities = await loop.run_in_executor(
                    None, inference.get_color_identities, db_url
                )

                # Score only color-legal cards
                scored = await loop.run_in_executor(
                    None,
                    lambda: inference.score_cards(
                        commander_id, context_ids, embeddings, model, all_ids,
                        color_identities=color_identities,
                    ),
                )

                if scored:
                    # ── Heuristic boost overrides ─────────────────────────────
                    # Apply score multipliers from commander analysis signals.
                    # "mana_producers": boost cards with mana-producing activated
                    # abilities (e.g. "{T}: Add {G}") — the "elfball" engine for
                    # commanders like Tyvar the Bellicose whose key mechanic is
                    # the MTG rules term "mana ability".
                    active_boosts: set[str] = set(boost_overrides or [])
                    if active_boosts:
                        # Fetch oracle texts for scored cards (non-land pool)
                        scored_ids = [cid for cid, _ in scored]
                        if scored_ids:
                            boost_result = await db.execute(
                                text("""
                                    SELECT id::text, oracle_text
                                    FROM cards
                                    WHERE id::text = ANY(:ids)
                                """),
                                {"ids": scored_ids},
                            )
                            oracle_texts: dict[str, str] = {
                                str(r[0]): (r[1] or "") for r in boost_result.fetchall()
                            }
                        else:
                            oracle_texts = {}

                        def _apply_boosts(pair: tuple[str, float]) -> tuple[str, float]:
                            cid, sc = pair
                            ot = oracle_texts.get(cid, "")
                            if "mana_producers" in active_boosts and _MANA_ADD_RE.search(ot):
                                sc = sc * _MANA_PRODUCER_BOOST
                            return cid, sc

                        scored = [_apply_boosts(p) for p in scored]

                    # ── Lookup tables (fetched in parallel) ───────────────────
                    (type_lines, cmc_map), (ramp_ids, guaranteed_ramp), land_staples = \
                        await asyncio.gather(
                            asyncio.gather(
                                loop.run_in_executor(None, inference.get_type_lines, db_url),
                                loop.run_in_executor(None, inference.get_cmc_map, db_url),
                            ),
                            loop.run_in_executor(None, inference.get_ramp_info, db_url),
                            loop.run_in_executor(None, inference.get_land_staple_ids, db_url),
                        )

                    def _is_land(cid: str) -> bool:
                        return "Land" in type_lines.get(cid, "")

                    def _is_basic(cid: str) -> bool:
                        tl = type_lines.get(cid, "")
                        return "Basic" in tl and "Land" in tl

                    # ── Tribal boost ──────────────────────────────────────────
                    # Read the commander's oracle text to detect which creature
                    # types it explicitly references (e.g. "Elves you control").
                    # Boost cards of those types — this catches tribal commanders
                    # even when training co-occurrence data is thin.
                    all_subtypes: frozenset[str] = frozenset(
                        subtype
                        for tl in type_lines.values()
                        if "\u2014" in tl
                        for subtype in tl.split("\u2014", 1)[1].split()
                    )
                    cmd_oracle_text: str = commander_row[4] or ""
                    cmd_tribal_types = _commander_tribal_types(cmd_oracle_text, all_subtypes)
                    if cmd_tribal_types:
                        scored = [
                            (
                                cid,
                                sc * TRIBAL_BOOST
                                if _card_subtypes(type_lines.get(cid, "")) & cmd_tribal_types
                                else sc,
                            )
                            for cid, sc in scored
                        ]
                        scored.sort(key=lambda x: x[1], reverse=True)

                    nonbasic_scored = [
                        (cid, sc) for cid, sc in scored
                        if _is_land(cid) and not _is_basic(cid)
                    ]
                    spell_scored = [
                        (cid, sc) for cid, sc in scored if not _is_land(cid)
                    ]

                    # ── Ramp selection ────────────────────────────────────────
                    # Force Sol Ring + Arcane Signet in first, then fill to
                    # RAMP_TARGET from top-scored ramp candidates.
                    score_lookup = {cid: sc for cid, sc in spell_scored}

                    selected_ramp: list[tuple[str, float]] = []
                    for name in GUARANTEED_RAMP:
                        cid = guaranteed_ramp.get(name)
                        if cid and cid in score_lookup:
                            selected_ramp.append((cid, score_lookup[cid]))

                    preselected_ids = {cid for cid, _ in selected_ramp}
                    ramp_candidates = [
                        (cid, sc) for cid, sc in spell_scored
                        if cid in ramp_ids and cid not in preselected_ids
                    ]
                    remaining = RAMP_TARGET - len(selected_ramp)
                    selected_ramp.extend(ramp_candidates[:remaining])
                    selected_ramp_ids = {cid for cid, _ in selected_ramp}

                    # ── Mana curve enforcement (ramp-aware) ───────────────────
                    # Ramp cards consume slots in their natural CMC bucket;
                    # non-ramp spells fill the remainder of each bucket target.
                    ramp_bucket_fill: dict[int, int] = {}
                    for cid, _ in selected_ramp:
                        b = _curve_bucket(cmc_map.get(cid, 0.0))
                        ramp_bucket_fill[b] = ramp_bucket_fill.get(b, 0) + 1

                    non_ramp_scored = [
                        (cid, sc) for cid, sc in spell_scored
                        if cid not in ramp_ids
                    ]
                    buckets: list[list[tuple[str, float]]] = [
                        [] for _ in CURVE_BUCKETS
                    ]
                    for cid, sc in non_ramp_scored:
                        b = _curve_bucket(cmc_map.get(cid, 0.0))
                        buckets[b].append((cid, sc))

                    selected_non_ramp: list[tuple[str, float]] = []
                    overflow: list[tuple[str, float]] = []
                    deficit = 0
                    for b, (_, target) in enumerate(CURVE_BUCKETS):
                        adjusted = max(0, target - ramp_bucket_fill.get(b, 0))
                        selected_non_ramp.extend(buckets[b][:adjusted])
                        overflow.extend(buckets[b][adjusted:])
                        if len(buckets[b]) < adjusted:
                            deficit += adjusted - len(buckets[b])

                    overflow.sort(key=lambda x: x[1], reverse=True)
                    selected_non_ramp.extend(overflow[:deficit])

                    selected_spells = (selected_ramp + selected_non_ramp)[:SPELL_SLOTS]

                    # ── Non-basic land selection ──────────────────────────────
                    # Force Command Tower + Exotic Orchard first (preserving their
                    # model scores), then fill remaining slots from scored pool.
                    nonbasic_score_map = {cid: sc for cid, sc in nonbasic_scored}
                    guaranteed_nb: list[tuple[str, float]] = []
                    for name in GUARANTEED_NONBASICS:
                        cid = land_staples.get(name)
                        if cid and cid in nonbasic_score_map:
                            guaranteed_nb.append((cid, nonbasic_score_map[cid]))
                        elif cid and _is_land(cid) and not _is_basic(cid):
                            # In DB but not scored (missing embedding) — include anyway
                            guaranteed_nb.append((cid, 0.0))

                    guaranteed_nb_ids = {cid for cid, _ in guaranteed_nb}
                    remaining_nonbasics = [
                        (cid, sc) for cid, sc in nonbasic_scored
                        if cid not in guaranteed_nb_ids
                    ]
                    selected_nonbasics = (
                        guaranteed_nb
                        + remaining_nonbasics[:NONBASIC_LAND_CAP - len(guaranteed_nb)]
                    )
                    basic_needed = LAND_TARGET - len(selected_nonbasics)

                    # Fetch metadata for spells + non-basic lands
                    fetch_ids = (
                        [cid for cid, _ in selected_spells]
                        + [cid for cid, _ in selected_nonbasics]
                    )
                    card_result = await db.execute(
                        text("""
                            SELECT id::text, oracle_id, name, type_line, oracle_text,
                                   color_identity, mana_cost, cmc
                            FROM cards
                            WHERE id::text = ANY(:ids)
                        """),
                        {"ids": fetch_ids},
                    )
                    card_map = {str(r[0]): r for r in card_result.fetchall()}

                    # ── Basic land distribution by mana pip ratios ────────────
                    commander_colors = [c for c in color_identity if c in COLOR_TO_BASIC]
                    if not commander_colors:
                        # Colorless commander — fill with Wastes
                        commander_colors = ["C"]
                    pip_totals: dict[str, int] = {}
                    for cid, _ in selected_spells:
                        if cid in card_map:
                            for color, cnt in _count_pips(card_map[cid][6]).items():
                                pip_totals[color] = pip_totals.get(color, 0) + cnt

                    relevant_pips = {c: pip_totals.get(c, 1) for c in commander_colors}
                    total_pips = sum(relevant_pips.values()) or 1

                    basic_counts: dict[str, int] = {}
                    if commander_colors and basic_needed > 0:
                        allocated = 0
                        for color in commander_colors:
                            cnt = int((relevant_pips[color] / total_pips) * basic_needed)
                            basic_counts[color] = cnt
                            allocated += cnt
                        remainder = basic_needed - allocated
                        for color in sorted(commander_colors, key=lambda c: relevant_pips[c], reverse=True):
                            if remainder <= 0:
                                break
                            basic_counts[color] += 1
                            remainder -= 1

                    # Fetch one representative row per basic land type needed
                    basic_names = [
                        COLOR_TO_BASIC[c] for c in commander_colors
                        if basic_counts.get(c, 0) > 0
                    ]
                    basic_rows: dict[str, tuple] = {}
                    if basic_names:
                        basic_result = await db.execute(
                            text("""
                                SELECT DISTINCT ON (name)
                                    name, id::text, oracle_id, type_line, oracle_text,
                                    color_identity, mana_cost, cmc
                                FROM cards
                                WHERE name = ANY(:names)
                                ORDER BY name, id
                            """),
                            {"names": basic_names},
                        )
                        basic_rows = {r[0]: r for r in basic_result.fetchall()}

                    # ── Assemble final deck ───────────────────────────────────
                    cards = []
                    scores = []

                    for cid, sc in selected_spells:
                        if cid in card_map:
                            r = card_map[cid]
                            cards.append({
                                "oracle_id": r[1], "name": r[2], "type_line": r[3],
                                "oracle_text": r[4], "color_identity": r[5] or [],
                                "mana_cost": r[6], "cmc": r[7], "count": 1,
                                "is_ramp": cid in selected_ramp_ids,
                            })
                            scores.append(float(sc))

                    for cid, sc in selected_nonbasics:
                        if cid in card_map:
                            r = card_map[cid]
                            cards.append({
                                "oracle_id": r[1], "name": r[2], "type_line": r[3],
                                "oracle_text": r[4], "color_identity": r[5] or [],
                                "mana_cost": r[6], "cmc": r[7], "count": 1,
                            })
                            scores.append(float(sc))

                    for color in commander_colors:
                        cnt = basic_counts.get(color, 0)
                        if cnt <= 0:
                            continue
                        name = COLOR_TO_BASIC[color]
                        if name in basic_rows:
                            r = basic_rows[name]
                            cards.append({
                                "oracle_id": r[2], "name": r[0], "type_line": r[3],
                                "oracle_text": r[4], "color_identity": r[5] or [],
                                "mana_cost": r[6], "cmc": r[7], "count": cnt,
                            })
                            scores.append(0.0)

                    # ── Role annotation ───────────────────────────────────────
                    for card in cards:
                        role_hits = detect_roles(
                            card.get("oracle_text") or "",
                            card.get("type_line") or "",
                        )
                        card["roles"] = [
                            {"role": role, "effect_class": ec}
                            for role, ec in role_hits
                        ]

                    non_land_cards = [
                        c for c in cards if "Land" not in c.get("type_line", "")
                    ]
                    archetype_info = detect_archetype(non_land_cards)

                    # Resolve context card names for the response
                    context_names: list[str] = []
                    if context_ids:
                        ctx_result = await db.execute(
                            text("""
                                SELECT id::text, name FROM cards
                                WHERE id::text = ANY(:ids)
                            """),
                            {"ids": context_ids},
                        )
                        ctx_name_map = {str(r[0]): r[1] for r in ctx_result.fetchall()}
                        context_names = [
                            ctx_name_map[cid] for cid in context_ids if cid in ctx_name_map
                        ]

                    return {
                        "commander": dict(commander_row._mapping),
                        "cards": cards,
                        "scores": scores,
                        "checkpoint": ckpt_name,
                        "context_cards": context_names,
                        "role_counts": archetype_info.get("role_counts", {}),
                        "archetype": archetype_info.get("archetype", ""),
                        "win_conditions": archetype_info.get("win_conditions", []),
                    }

    except Exception as exc:
        log.warning("Model inference failed, falling back to synergy stub: %s", exc)

    # ── Synergy-based fallback stub ───────────────────────────────────────────
    log.warning(
        "Using synergy-based stub for commander %s (checkpoint=%s unavailable)",
        commander_oracle_id, checkpoint,
    )
    card_result = await db.execute(
        text("""
            SELECT DISTINCT ON (c.oracle_id)
                c.oracle_id, c.name, c.type_line, c.oracle_text,
                c.color_identity, c.mana_cost, c.cmc,
                coalesce(s.score, 0.0) AS score
            FROM cards c
            LEFT JOIN synergy_edges s
                ON (s.card_a = :cid OR s.card_b = :cid)
                AND (s.card_a = c.id OR s.card_b = c.id)
                AND s.score_type = 'ability_trigger'
            WHERE c.id != :cid
              AND c.color_identity <@ :ci::text[]
              AND c.legalities->>'commander' = 'legal'
            ORDER BY c.oracle_id, score DESC
            LIMIT 99
        """),
        {"cid": commander_id, "ci": color_identity},
    )
    rows = card_result.fetchall()

    cards = [
        {
            "oracle_id": r[0], "name": r[1], "type_line": r[2],
            "oracle_text": r[3], "color_identity": r[4],
            "mana_cost": r[5], "cmc": r[6],
        }
        for r in rows
    ]
    scores = [float(r[7]) for r in rows]

    return {
        "commander": dict(commander_row._mapping),
        "cards": cards,
        "scores": scores,
        "checkpoint": checkpoint,
        "context_cards": [],
    }
