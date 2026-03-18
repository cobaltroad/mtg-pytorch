"""
Package-aware combo boost for deck generation.

After the base per-card scoring pass, this module:
  1. Identifies which combo_packages have ≥1 card already selected into
     the partial deck ("triggered" packages).
  2. Filters to packages meeting a minimum completion ratio.
  3. Ranks by (completion × package_weight) and takes the top N.
  4. Boosts missing combo members by α × completion × weight, capped per card.

Usage
-----
    from ops.combo_boost import apply_combo_boost

    scored, triggered = await apply_combo_boost(
        db, scored, selected_card_ids, commander_color_identity, combo_boost=0.3
    )

Set combo_boost=0.0 to disable entirely (no-op, no DB queries).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

# Only packages at least this complete contribute to score boosts.
# Packages below the floor still appear in triggered[] for visibility
# but are flagged boost_applied=False.
MIN_COMPLETION = 0.5

# Maximum number of packages that apply boosts.  Sorted by completion×weight
# descending so the strongest signals win.
MAX_BOOST_PACKAGES = 25

# Features that earn a higher package weight
_WIN_FEATURES      = frozenset({"Win the game", "Exile all cards from target player's library"})
_INFINITE_FEATURES = frozenset({
    "Infinite mana", "Infinite damage", "Infinite draws",
    "Infinite tokens", "Infinite life", "Infinite storm count",
    "Infinite combat phases", "Infinite ETB", "Infinite death triggers",
    "Infinite colored mana", "Infinite colorless mana", "Infinite creature tokens",
})


def _package_weight(produces: list[str]) -> float:
    """Derive a weight scalar from the set of outcomes a combo produces."""
    for f in produces:
        if f in _WIN_FEATURES:
            return 2.0
    for f in produces:
        if f in _INFINITE_FEATURES:
            return 1.5
    return 1.0


async def apply_combo_boost(
    db: AsyncSession,
    scored: list[tuple[str, float]],
    selected_card_ids: list[str],
    commander_color_identity: list[str],
    combo_boost: float = 0.3,
) -> tuple[list[tuple[str, float]], list[dict]]:
    """
    Apply package-aware score boosts and return (boosted_scored, triggered_packages).

    Parameters
    ----------
    db:
        Async SQLAlchemy session.
    scored:
        List of (card_id, score) pairs from the base scoring pass.
    selected_card_ids:
        Card IDs already committed to the partial deck.
    commander_color_identity:
        Commander's color identity (e.g. ["G", "B"]).  Combos outside this
        identity are excluded via SQL.
    combo_boost:
        Scalar α for the boost formula.  Set to 0.0 to disable.

    Returns
    -------
    scored:
        The same list with scores adjusted for triggered combos.
    triggered:
        List of dicts (all triggered packages, not just those that boosted),
        each with a ``boost_applied`` flag.
    """
    if combo_boost == 0.0 or not selected_card_ids:
        return scored, []

    cmd_colors = list(commander_color_identity) or ["C"]

    # ── 1. Single query: find triggered packages + all their member cards ──────
    # Join combo_packages → combo_package_cards once, aggregate in SQL.
    # Color identity filter is applied server-side to avoid shipping rows
    # that can never qualify.
    result = await db.execute(
        text("""
            WITH triggered AS (
                SELECT
                    cp.id                                                         AS pkg_id,
                    cp.spellbook_id,
                    cp.produces,
                    cp.identity,
                    COUNT(cpc.id) FILTER (WHERE NOT cpc.is_template)              AS total_required,
                    COUNT(cpc.id) FILTER (
                        WHERE NOT cpc.is_template
                        AND cpc.card_id::text = ANY(:selected)
                    )                                                              AS cards_present
                FROM combo_packages cp
                JOIN combo_package_cards cpc ON cpc.combo_package_id = cp.id
                WHERE cp.legal_commander = TRUE
                  AND cp.spoiler = FALSE
                  -- color identity subset check: every char in identity must be
                  -- in cmd_colors or be 'C'
                  AND (
                      cp.identity = 'C'
                      OR (
                          SELECT bool_and(ch = ANY(:cmd_colors) OR ch = 'C')
                          FROM regexp_split_to_table(cp.identity, '') AS ch
                      )
                  )
                GROUP BY cp.id, cp.spellbook_id, cp.produces, cp.identity
                HAVING COUNT(cpc.id) FILTER (
                    WHERE NOT cpc.is_template
                    AND cpc.card_id::text = ANY(:selected)
                ) >= 1
            )
            SELECT
                t.pkg_id::text,
                t.spellbook_id,
                t.produces,
                t.identity,
                t.total_required,
                t.cards_present,
                -- all non-template member cards as parallel arrays
                array_agg(cpc.card_id::text)          FILTER (WHERE NOT cpc.is_template) AS all_card_ids,
                array_agg(cpc.spellbook_card_name)    FILTER (WHERE NOT cpc.is_template) AS all_card_names,
                array_agg(cpc.card_id::text = ANY(:selected))
                                                       FILTER (WHERE NOT cpc.is_template) AS all_present
            FROM triggered t
            JOIN combo_package_cards cpc ON cpc.combo_package_id = t.pkg_id
            GROUP BY t.pkg_id, t.spellbook_id, t.produces, t.identity,
                     t.total_required, t.cards_present
        """),
        {"selected": selected_card_ids, "cmd_colors": cmd_colors},
    )
    rows = result.fetchall()

    if not rows:
        return scored, []

    # ── 2. Compute completion, rank, split into boosting vs display-only ───────
    candidates: list[dict] = []
    for row in rows:
        total    = int(row[4]) if row[4] else 0
        present  = int(row[5]) if row[5] else 0
        if total == 0:
            continue
        produces    = list(row[2] or [])
        completion  = present / total
        pkg_weight  = _package_weight(produces)

        card_ids   = row[6] or []
        card_names = row[7] or []
        is_present = row[8] or []

        included_names = [n for n, p in zip(card_names, is_present) if p]
        missing_names  = [n for n, p in zip(card_names, is_present) if not p]
        missing_ids    = [c for c, p in zip(card_ids, is_present) if not p and c]

        candidates.append({
            "pkg_id":         row[0],
            "spellbook_id":   row[1],
            "produces":       produces,
            "completion":     completion,
            "pkg_weight":     pkg_weight,
            "priority":       completion * pkg_weight,
            "included_names": included_names,
            "missing_names":  missing_names,
            "missing_ids":    missing_ids,
        })

    # Sort by priority descending so strongest signals take the top slots
    candidates.sort(key=lambda c: c["priority"], reverse=True)

    # ── 3. Apply boosts from top-N packages that meet the completion floor ─────
    boost_map: dict[str, float] = {}
    max_boost_per_card = combo_boost * 2.0   # cap cumulative boost on any single card

    boosting = [c for c in candidates if c["completion"] >= MIN_COMPLETION][:MAX_BOOST_PACKAGES]
    boosting_ids = {c["pkg_id"] for c in boosting}

    for c in boosting:
        addend = combo_boost * c["completion"] * c["pkg_weight"]
        for cid in c["missing_ids"]:
            current = boost_map.get(cid, 0.0)
            boost_map[cid] = min(current + addend, max_boost_per_card)

    # ── 4. Build response metadata ────────────────────────────────────────────
    triggered: list[dict] = []
    for c in candidates:
        triggered.append({
            "spellbook_id":   c["spellbook_id"],
            "produces":       c["produces"],
            "cards_included": c["included_names"],
            "cards_missing":  c["missing_names"],
            "completion":     round(c["completion"], 3),
            "package_weight": c["pkg_weight"],
            "boost_applied":  c["pkg_id"] in boosting_ids,
        })

    if not boost_map:
        return scored, triggered

    log.info(
        "Combo boost: %d triggered (%d boosting, %d below %.0f%% floor), "
        "boosting %d candidate cards",
        len(candidates), len(boosting), len(candidates) - len(boosting),
        MIN_COMPLETION * 100, len(boost_map),
    )

    # ── 5. Apply boosts and re-sort ───────────────────────────────────────────
    scored = [(cid, sc + boost_map.get(cid, 0.0)) for cid, sc in scored]
    scored.sort(key=lambda x: x[1], reverse=True)

    return scored, triggered
