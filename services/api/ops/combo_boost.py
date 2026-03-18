"""
Package-aware combo boost for deck generation.

After the base per-card scoring pass, this module:
  1. Identifies which combo_packages have ≥1 card already selected into
     the partial deck ("triggered" packages).
  2. Computes a completion ratio for each triggered package.
  3. Boosts the scores of cards that would complete or advance those packages,
     proportional to how complete the combo already is and how powerful the
     outcome is (Win the game > Infinite * > other).

Usage
-----
    from ops.combo_boost import apply_combo_boost

    scored, triggered = await apply_combo_boost(
        db, scored, selected_card_ids, commander_color_identity, combo_boost=0.3
    )

The `triggered` list is included verbatim in the generation response so the
caller can see which combos were detected and how complete they are.

Set combo_boost=0.0 to disable entirely (no-op, no DB queries).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

# Features that earn a higher package weight
_WIN_FEATURES      = frozenset({"Win the game", "Exile all cards from target player's library"})
_INFINITE_FEATURES = frozenset({
    "Infinite mana", "Infinite damage", "Infinite draws",
    "Infinite tokens", "Infinite life", "Infinite storm count",
    "Infinite combat phases", "Infinite ETB", "Infinite death triggers",
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
        List of (card_id, score) pairs from the base scoring pass — will be
        mutated in-place and returned.
    selected_card_ids:
        Card IDs already committed to the partial deck (used to detect
        triggered combos and compute completion ratios).
    commander_color_identity:
        Commander's color identity as a list of single-character strings
        (e.g. ["U", "B"]).  Only combos whose identity fits within this
        set are considered.
    combo_boost:
        Scalar α applied to the boost formula.  Set to 0.0 to disable.

    Returns
    -------
    scored:
        The same list with scores adjusted for triggered combos.
    triggered:
        List of dicts describing each triggered package, suitable for
        inclusion in the API response.
    """
    if combo_boost == 0.0 or not selected_card_ids:
        return scored, []

    # ── 1. Find combos that contain at least one card already in the deck ─────
    triggered_rows = await db.execute(
        text("""
            SELECT
                cp.id::text              AS pkg_id,
                cp.spellbook_id,
                cp.produces,
                cp.identity,
                -- count of non-template required cards
                COUNT(cpc.id) FILTER (WHERE NOT cpc.is_template)          AS total_required,
                -- count of those already present in the partial deck
                COUNT(cpc.id) FILTER (
                    WHERE NOT cpc.is_template
                    AND cpc.card_id::text = ANY(:selected)
                )                                                           AS cards_present
            FROM combo_packages cp
            JOIN combo_package_cards cpc ON cpc.combo_package_id = cp.id
            WHERE cp.legal_commander = TRUE
              AND cp.spoiler = FALSE
            GROUP BY cp.id, cp.spellbook_id, cp.produces, cp.identity
            HAVING COUNT(cpc.id) FILTER (
                WHERE NOT cpc.is_template
                AND cpc.card_id::text = ANY(:selected)
            ) >= 1
        """),
        {"selected": selected_card_ids},
    )
    triggered_combos = triggered_rows.fetchall()

    if not triggered_combos:
        return scored, []

    # Build commander color set for identity filtering
    cmd_colors = frozenset(commander_color_identity)

    # ── 2. For each triggered combo, compute completion and find missing cards ─
    boost_map: dict[str, float] = {}   # card_id → cumulative boost addend
    triggered: list[dict] = []

    for row in triggered_combos:
        pkg_id        = row[0]
        spellbook_id  = row[1]
        produces      = list(row[2] or [])
        identity      = row[3] or ""
        total_required = int(row[4]) if row[4] else 0
        cards_present  = int(row[5]) if row[5] else 0

        # Skip combos whose color identity doesn't fit within the commander's
        combo_colors = frozenset(identity) - {"C"}
        if not combo_colors.issubset(cmd_colors):
            continue

        if total_required == 0:
            continue

        completion   = cards_present / total_required
        pkg_weight   = _package_weight(produces)

        # Fetch names of included and missing cards for the response metadata
        member_rows = await db.execute(
            text("""
                SELECT
                    cpc.card_id::text,
                    cpc.spellbook_card_name,
                    cpc.is_template,
                    cpc.card_id::text = ANY(:selected) AS is_present
                FROM combo_package_cards cpc
                WHERE cpc.combo_package_id = :pkg_id
                  AND NOT cpc.is_template
            """),
            {"pkg_id": pkg_id, "selected": selected_card_ids},
        )
        members = member_rows.fetchall()

        included_names = [m[1] for m in members if m[3]]
        missing_names  = [m[1] for m in members if not m[3]]
        missing_ids    = [m[0] for m in members if not m[3] and m[0]]

        # Accumulate boost for missing combo members
        boost_addend = combo_boost * completion * pkg_weight
        for cid in missing_ids:
            boost_map[cid] = boost_map.get(cid, 0.0) + boost_addend

        triggered.append({
            "spellbook_id":    spellbook_id,
            "produces":        produces,
            "cards_included":  included_names,
            "cards_missing":   missing_names,
            "completion":      round(completion, 3),
            "package_weight":  pkg_weight,
        })

    if not boost_map:
        return scored, triggered

    log.info(
        "Combo boost: %d triggered packages, boosting %d candidate cards",
        len(triggered), len(boost_map),
    )

    # ── 3. Apply boosts to the scored list ───────────────────────────────────
    scored = [(cid, sc + boost_map.get(cid, 0.0)) for cid, sc in scored]
    scored.sort(key=lambda x: x[1], reverse=True)

    return scored, triggered
