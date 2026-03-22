"""Deck generation orchestrator.

Handles model inference, assembles the card pool, then calls behavioural
scoring modules (ramp, evasion, removal, value_engine) before enforcing
structural constraints (curve, land budget, basics).  This file owns the
*what goes in the deck* decision; the scoring modules own *why*.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .signals import build_signals
from .ramp import score_mana_producers, score_land_mana_quality, select_ramp
from .evasion import score_evasion_enablers
from .removal import score_removal
from .value_engine import score_value_engine

log = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# ── Deck structure constants ──────────────────────────────────────────────────

LAND_TARGET      = 36
SPELL_SLOTS      = 99 - LAND_TARGET        # 63 model-scored non-land cards
NONBASIC_LAND_CAP = 20
RAMP_TARGET      = 10

GUARANTEED_NONBASICS = ("Command Tower", "Exotic Orchard")
GUARANTEED_RAMP_NAMES = ("Sol Ring", "Arcane Signet")

COLOR_TO_BASIC = {
    "W": "Plains",
    "U": "Island",
    "B": "Swamp",
    "R": "Mountain",
    "G": "Forest",
    "C": "Wastes",
}

CURVE_BUCKETS: list[tuple[int, int]] = [
    (1,   8),
    (2,  16),
    (3,  14),
    (4,  12),
    (5,   7),
    (999, 6),
]
assert sum(t for _, t in CURVE_BUCKETS) == SPELL_SLOTS

TRIBAL_BOOST = 1.5

# Commander-value cards (Fierce Guardianship, Jeska's Will, etc.)
_COMMANDER_VALUE_RE = re.compile(
    r"if you control a commander"
    r"|as long as you control a commander"
    r"|while you control a commander",
    re.I,
)
_LEGEND_MANA_RE = re.compile(
    r"legendary (creature|planeswalker).{0,60}(mana|add)"
    r"|add.{0,30}legendary (creature|planeswalker)",
    re.I,
)
_COMMANDER_VALUE_BOOST = 1.4

_PLURAL_IRREGULARS: dict[str, str] = {"elves": "elf", "wolves": "wolf"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _curve_bucket(cmc: float) -> int:
    for i, (cap, _) in enumerate(CURVE_BUCKETS):
        if cmc <= cap:
            return i
    return len(CURVE_BUCKETS) - 1


def _card_subtypes(type_line: str) -> frozenset[str]:
    if "\u2014" in type_line:
        return frozenset(type_line.split("\u2014", 1)[1].split())
    return frozenset()


def _commander_tribal_types(oracle_text: str, known_subtypes: frozenset[str]) -> frozenset[str]:
    known_lower: dict[str, str] = {s.lower(): s for s in known_subtypes}
    found: set[str] = set()
    for word in re.findall(r"\b[A-Z][a-z]+\b", oracle_text or ""):
        lower = word.lower()
        if lower in known_lower:
            found.add(known_lower[lower])
            continue
        singular = _PLURAL_IRREGULARS.get(lower)
        if singular and singular in known_lower:
            found.add(known_lower[singular])
            continue
        if lower.endswith("s") and lower[:-1] in known_lower:
            found.add(known_lower[lower[:-1]])
    return frozenset(found)


def _count_pips(mana_cost: str | None) -> dict[str, int]:
    pips: dict[str, int] = {}
    for ch in re.findall(r"\{([WUBRG])\}", mana_cost or ""):
        pips[ch] = pips.get(ch, 0) + 1
    return pips


# ── Main entry point ──────────────────────────────────────────────────────────

async def generate(
    db: AsyncSession,
    commander_oracle_id: UUID,
    checkpoint: str,
    boost_overrides: list[str] | None = None,
    combo_boost: float = 0.3,
    partner_oracle_id: UUID | None = None,
) -> dict | None:
    """Generate a 99-card Commander deck.

    Runs model inference to score the card pool, then applies behavioural
    scoring adjustments (ramp, evasion, removal, card draw) before
    enforcing land budget, mana curve, and guaranteed inclusions.
    Falls back to synergy stub if no model checkpoint is available.
    """
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

    commander_id   = str(commander_row[0])
    color_identity = list(commander_row[5] or [])

    partner_id: str | None = None
    if partner_oracle_id is not None:
        p = await db.execute(
            text("SELECT id, color_identity FROM cards WHERE oracle_id = :oid"),
            {"oid": str(partner_oracle_id)},
        )
        p_row = p.fetchone()
        if p_row:
            partner_id = str(p_row[0])
            for c in (p_row[1] or []):
                if c not in color_identity:
                    color_identity.append(c)

    db_url = DATABASE_URL

    try:
        from ops import inference
        from ops.card_roles import detect_roles
        from ops.import_utils import detect_archetype

        loop = asyncio.get_event_loop()

        ckpt_name = checkpoint if checkpoint != "latest" else "phase4_best"
        model = await loop.run_in_executor(None, inference.get_model, ckpt_name)

        if model is not None:
            embeddings = await loop.run_in_executor(None, inference.get_embeddings, db_url)

            if embeddings and commander_id in embeddings:
                context_ids = await loop.run_in_executor(
                    None, inference.get_common_context, commander_id, db_url
                )

                proxy_context: bool = False
                if not context_ids:
                    context_ids = await loop.run_in_executor(
                        None,
                        lambda: inference.get_proxy_context_from_similar_commanders(
                            commander_id, db_url, embeddings
                        ),
                    )
                    proxy_context = bool(context_ids)
                    if proxy_context:
                        log.info(
                            "Using proxy context (%d cards) for unseen commander %s",
                            len(context_ids), commander_id,
                        )

                legal_ids = await loop.run_in_executor(None, inference.get_legal_ids, db_url)
                all_ids = [cid for cid in embeddings if cid in legal_ids]

                color_identities = await loop.run_in_executor(
                    None, inference.get_color_identities, db_url
                )

                scored = await loop.run_in_executor(
                    None,
                    lambda: inference.score_cards(
                        commander_id, context_ids, embeddings, model, all_ids,
                        color_identities=color_identities,
                        partner_ids=[partner_id] if partner_id else None,
                    ),
                )

                if scored:
                    # ── Fetch oracle texts (once, for all scorers) ────────────
                    scored_ids = [cid for cid, _ in scored]
                    ot_result = await db.execute(
                        text("SELECT id::text, oracle_text FROM cards WHERE id::text = ANY(:ids)"),
                        {"ids": scored_ids},
                    )
                    oracle_texts: dict[str, str] = {
                        str(r[0]): (r[1] or "") for r in ot_result.fetchall()
                    }

                    # ── Lookup tables ─────────────────────────────────────────
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

                    # ── Tribal types ──────────────────────────────────────────
                    all_subtypes: frozenset[str] = frozenset(
                        sub
                        for tl in type_lines.values()
                        if "\u2014" in tl
                        for sub in tl.split("\u2014", 1)[1].split()
                    )
                    cmd_oracle_text: str = commander_row[4] or ""
                    cmd_type_line:   str = commander_row[3] or ""
                    cmd_tribal_types = _commander_tribal_types(cmd_oracle_text, all_subtypes)
                    if "Creature" in cmd_type_line:
                        cmd_tribal_types = cmd_tribal_types | (
                            _card_subtypes(cmd_type_line) & all_subtypes
                        )

                    # ── Build deck signals ────────────────────────────────────
                    signals = build_signals(
                        cmd_oracle_text, color_identity, cmd_tribal_types, boost_overrides
                    )

                    # ── Behavioural scoring passes ────────────────────────────
                    # Applied to all candidates before the land/spell split.
                    # Each scorer is card-type-agnostic — it boosts whatever
                    # serves the described deckbuilding function.
                    scored = score_mana_producers(scored, oracle_texts, signals)
                    scored = score_removal(scored, oracle_texts, signals)
                    scored = score_value_engine(scored, oracle_texts, signals)
                    scored = score_evasion_enablers(scored, oracle_texts, signals)

                    # Commander-value boost (inline: Fierce Guardianship, etc.)
                    if "commander_value" in signals.active_boosts:
                        scored = [
                            (cid, sc * _COMMANDER_VALUE_BOOST)
                            if (_COMMANDER_VALUE_RE.search(oracle_texts.get(cid, ""))
                                or _LEGEND_MANA_RE.search(oracle_texts.get(cid, "")))
                            else (cid, sc)
                            for cid, sc in scored
                        ]

                    # ── Tribal boost ──────────────────────────────────────────
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

                    # ── Combo package boost ───────────────────────────────────
                    triggered_combos: list[dict] = []
                    if combo_boost > 0.0 and context_ids:
                        from ops.combo_boost import apply_combo_boost
                        scored, triggered_combos = await apply_combo_boost(
                            db, scored,
                            [commander_id] + list(context_ids),
                            color_identity,
                            combo_boost=combo_boost,
                        )

                    # ── Split into lands and spells ───────────────────────────
                    nonbasic_scored = [
                        (cid, sc) for cid, sc in scored
                        if _is_land(cid) and not _is_basic(cid)
                    ]
                    spell_scored = [
                        (cid, sc) for cid, sc in scored if not _is_land(cid)
                    ]

                    # ── Land quality scoring ──────────────────────────────────
                    # Colorless-mana penalty and evasion-land synergy run after
                    # the split since they're land-specific adjustments.
                    nonbasic_scored = score_land_mana_quality(nonbasic_scored, oracle_texts, signals)
                    nonbasic_scored.sort(key=lambda x: x[1], reverse=True)

                    # ── Ramp selection ────────────────────────────────────────
                    selected_ramp, selected_ramp_ids = select_ramp(
                        spell_scored, ramp_ids, guaranteed_ramp, RAMP_TARGET
                    )

                    # ── Mana curve enforcement ────────────────────────────────
                    ramp_bucket_fill: dict[int, int] = {}
                    for cid, _ in selected_ramp:
                        b = _curve_bucket(cmc_map.get(cid, 0.0))
                        ramp_bucket_fill[b] = ramp_bucket_fill.get(b, 0) + 1

                    non_ramp_scored = [
                        (cid, sc) for cid, sc in spell_scored
                        if cid not in ramp_ids
                    ]
                    buckets: list[list[tuple[str, float]]] = [[] for _ in CURVE_BUCKETS]
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
                    nonbasic_score_map = {cid: sc for cid, sc in nonbasic_scored}
                    guaranteed_nb: list[tuple[str, float]] = []
                    for name in GUARANTEED_NONBASICS:
                        cid = land_staples.get(name)
                        if cid and cid in nonbasic_score_map:
                            guaranteed_nb.append((cid, nonbasic_score_map[cid]))
                        elif cid and _is_land(cid) and not _is_basic(cid):
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

                    # ── Fetch card metadata ───────────────────────────────────
                    fetch_ids = (
                        [cid for cid, _ in selected_spells]
                        + [cid for cid, _ in selected_nonbasics]
                    )
                    card_result = await db.execute(
                        text("""
                            SELECT id::text, oracle_id, name, type_line, oracle_text,
                                   color_identity, mana_cost, cmc
                            FROM cards WHERE id::text = ANY(:ids)
                        """),
                        {"ids": fetch_ids},
                    )
                    card_map = {str(r[0]): r for r in card_result.fetchall()}

                    # ── Basic land distribution by pip ratio ──────────────────
                    commander_colors = [c for c in color_identity if c in COLOR_TO_BASIC]
                    if not commander_colors:
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

                    basic_names = [
                        COLOR_TO_BASIC[c] for c in commander_colors
                        if basic_counts.get(c, 0) > 0
                    ]
                    basic_rows: dict[str, tuple] = {}
                    if basic_names:
                        basic_result = await db.execute(
                            text("""
                                SELECT DISTINCT ON (split_part(name, ' // ', 1))
                                    split_part(name, ' // ', 1) AS canonical,
                                    id::text, oracle_id, type_line, oracle_text,
                                    color_identity, mana_cost, cmc
                                FROM cards
                                WHERE split_part(name, ' // ', 1) = ANY(:names)
                                ORDER BY split_part(name, ' // ', 1), id
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

                    # ── Intra-deck synergy density ────────────────────────────
                    spell_ids = [cid for cid, _ in selected_spells]
                    syn_result = await db.execute(
                        text("""
                            SELECT AVG(score) FROM synergy_edges
                            WHERE card_a = ANY(CAST(:ids AS uuid[]))
                              AND card_b = ANY(CAST(:ids AS uuid[]))
                        """),
                        {"ids": spell_ids},
                    )
                    synergy_density = float(syn_result.scalar() or 0.0)

                    baseline_result = await db.execute(
                        text("SELECT AVG(score) FROM synergy_edges TABLESAMPLE SYSTEM(0.1)")
                    )
                    synergy_baseline = float(baseline_result.scalar() or 0.0)

                    context_names: list[str] = []
                    if context_ids:
                        ctx_result = await db.execute(
                            text("SELECT id::text, name FROM cards WHERE id::text = ANY(:ids)"),
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
                        "proxy_context": proxy_context,
                        "role_counts": archetype_info.get("role_counts", {}),
                        "archetype": archetype_info.get("archetype", ""),
                        "win_conditions": archetype_info.get("win_conditions", []),
                        "combo_packages_triggered": triggered_combos,
                        "synergy_density": round(synergy_density, 4),
                        "synergy_baseline": round(synergy_baseline, 4),
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
              AND c.color_identity <@ CAST(:ci AS text[])
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
    return {
        "commander": dict(commander_row._mapping),
        "cards": cards,
        "scores": [float(r[7]) for r in rows],
        "checkpoint": checkpoint,
        "context_cards": [],
    }
