"""
deck_composition_profile.py — Issue #62

Research spike: derive deck-composition targets from the imported deck pool.

For every non-land spell slot in every imported deck, applies the same
role-detection patterns used by the ingest pipeline to count how many
ramp, draw, removal, tutor, protection, interaction, recursion, token,
win-condition, and evasion cards each deck runs.  Results are aggregated
into p25/p50/p75 percentile distributions, stratified by:

  - global (all decks)
  - color count (1–5 colors)
  - archetype (midrange / combo / control / aggro / stax / tokens / …)
  - commander type (creature vs. planeswalker)

Also reports tribal composition fractions (fraction of spell slots that
share the commander's tribal creature type) for decks with a tribal
archetype.

Outputs
-------
  $OUTPUT_FILE (default: /data/deck_composition_profile.json)

    Top-level ``targets`` dict — flat integer medians, drop-in
    replacements for the hardcoded constants in generate.py.

    ``details`` dict — full p25/p50/p75/mean/n per role per stratum,
    suitable for future dynamic lookup.

Usage
-----
    # All decks:
    docker compose run --rm ingest python deck_composition_profile.py

    # Custom output path:
    OUTPUT_FILE=/tmp/profile.json \\
        docker compose run --rm ingest python deck_composition_profile.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
OUTPUT_FILE  = Path(os.environ.get("OUTPUT_FILE", "/data/deck_composition_profile.json"))

# ── Role buckets reported in the profile ─────────────────────────────────────
# Fine-grained roles from synergy/roles.py are collapsed into these buckets.
# Each bucket maps one-to-one to a target in generate.py.

_FINE_TO_BUCKET: dict[str, str] = {
    "ramp":            "ramp",
    "draw_one":        "draw",
    "repeatable_draw": "draw",
    "removal":         "removal",
    "sweeper":         "removal",
    "tutor":           "tutor",
    "protection":      "protection",
    "interaction":     "interaction",
    "token_generator": "token",
    "recursion":       "recursion",
    "win_condition":   "win_condition",
    "evasion":         "evasion",   # synthetic bucket — see _card_roles() below
    # anthem, combat_trick excluded from targets (nice-to-have, not structural)
}

REPORT_ROLES: list[str] = [
    "ramp", "draw", "removal", "tutor",
    "protection", "interaction", "token", "recursion",
    "win_condition", "evasion",
]

# Permanent evasion keywords (as they appear in the cards.keywords[] column).
# Distinct from combat_trick (temporary grants until EOT).
_EVASION_KEYWORDS: frozenset[str] = frozenset({
    "flying", "menace", "shadow", "fear", "intimidate",
    "horsemanship", "skulk", "reach",
})


# ── Role-pattern compilation ──────────────────────────────────────────────────

def _compile_role_patterns() -> list[tuple[re.Pattern[str], str]]:
    """Import ROLE_PATTERNS from synergy.roles and compile them."""
    from synergy.roles import ROLE_PATTERNS  # noqa: PLC0415
    return [(re.compile(pat, re.IGNORECASE), role) for pat, role, _effect in ROLE_PATTERNS]


_COMPILED: list[tuple[re.Pattern[str], str]] | None = None


def _card_roles(oracle_text: str, type_line: str, keywords: list[str]) -> set[str]:
    """Return the set of *bucketed* roles for a single non-land card."""
    global _COMPILED
    if _COMPILED is None:
        _COMPILED = _compile_role_patterns()

    text    = oracle_text or ""
    fine: set[str] = set()

    for pat, role in _COMPILED:
        if role in fine:
            continue
        if pat.search(text):
            fine.add(role)

    # Evasion: permanent keyword on the card (not a temporary grant)
    kws_lower = {k.lower() for k in (keywords or [])}
    if kws_lower & _EVASION_KEYWORDS:
        fine.add("evasion")

    # Collapse to bucketed roles; each card counts at most once per bucket
    bucketed: set[str] = set()
    for fr in fine:
        b = _FINE_TO_BUCKET.get(fr)
        if b:
            bucketed.add(b)

    return bucketed


# ── Statistics helpers ────────────────────────────────────────────────────────

def _pct(values: list[float], q: float) -> float:
    """Linear-interpolation percentile, q ∈ [0, 1]."""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    pos = q * (n - 1)
    lo  = int(pos)
    hi  = min(lo + 1, n - 1)
    return s[lo] + (pos - lo) * (s[hi] - s[lo])


def _summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "p25": 0.0, "p50": 0.0, "p75": 0.0, "mean": 0.0}
    return {
        "n":    len(values),
        "p25":  round(_pct(values, 0.25), 1),
        "p50":  round(_pct(values, 0.50), 1),
        "p75":  round(_pct(values, 0.75), 1),
        "mean": round(sum(values) / len(values), 1),
    }


def _median_int(values: list[float]) -> int:
    return int(round(_pct(values, 0.50)))


# ── DB helpers ────────────────────────────────────────────────────────────────

def _asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:  # noqa: C901
    dsn  = _asyncpg_dsn(DATABASE_URL)
    conn = await asyncpg.connect(dsn)

    try:
        # ── 1. Load all decks ─────────────────────────────────────────────────
        deck_rows = await conn.fetch("""
            SELECT
                d.id::text           AS deck_id,
                d.card_ids,
                d.metadata,
                c.color_identity     AS cmd_colors,
                c.type_line          AS cmd_type_line,
                c.name               AS commander_name
            FROM decks d
            JOIN cards c ON c.id = d.commander_id
        """)

        if not deck_rows:
            log.error("No decks found — import decklists first.")
            return

        log.info("Loaded %d decks", len(deck_rows))

        # ── 2. Collect all card IDs and batch-fetch card data ─────────────────
        all_card_ids: set[str] = set()
        for row in deck_rows:
            ids = row["card_ids"] or []
            all_card_ids.update(str(i) for i in ids)

        card_rows = await conn.fetch("""
            SELECT
                id::text       AS id,
                oracle_text,
                type_line,
                keywords,
                name
            FROM cards
            WHERE id::text = ANY($1::text[])
        """, list(all_card_ids))

        card_map: dict[str, dict] = {
            r["id"]: {
                "oracle_text": r["oracle_text"] or "",
                "type_line":   r["type_line"]   or "",
                "keywords":    list(r["keywords"] or []),
                "name":        r["name"],
            }
            for r in card_rows
        }

        log.info("Fetched %d distinct cards", len(card_map))

        # ── 3. Pre-compute per-card roles (memoised) ──────────────────────────
        card_roles: dict[str, frozenset[str]] = {}
        for cid, c in card_map.items():
            if "Land" in c["type_line"]:
                card_roles[cid] = frozenset()   # lands don't contribute to spell-slot counts
            else:
                card_roles[cid] = frozenset(_card_roles(
                    c["oracle_text"], c["type_line"], c["keywords"]
                ))

        # ── 4. Per-deck analysis ──────────────────────────────────────────────
        # role_counts[bucket][role] = [count_per_deck, …]
        role_counts: dict[str, dict[str, list[float]]] = defaultdict(
            lambda: defaultdict(list)
        )

        # tribal_fracs[archetype_tag] = [fraction_of_spell_slots, …]
        tribal_fracs: dict[str, list[float]] = defaultdict(list)

        deck_log: list[dict] = []   # for the human-readable summary

        for row in deck_rows:
            card_ids  = [str(i) for i in (row["card_ids"] or [])]
            metadata  = row["metadata"] or {}
            if isinstance(metadata, str):
                metadata = json.loads(metadata)

            color_identity  = list(row["cmd_colors"] or [])
            cmd_type_line   = row["cmd_type_line"] or ""
            commander_name  = row["commander_name"]

            archetype       = metadata.get("archetype", "unknown") or "unknown"
            color_count     = len(color_identity)
            commander_type  = "planeswalker" if "Planeswalker" in cmd_type_line else "creature"

            # Tribal sub-type extraction: "tribal_zombies" → "zombie"
            is_tribal   = archetype.startswith("tribal_")
            tribal_type = archetype[len("tribal_"):].rstrip("s") if is_tribal else None

            # Accumulate role counts over spell slots
            per_role: dict[str, int] = defaultdict(int)
            spell_slots   = 0
            tribal_count  = 0

            for cid in card_ids:
                c = card_map.get(cid)
                if c is None:
                    continue
                if "Land" in c["type_line"]:
                    continue

                spell_slots += 1
                for role in card_roles[cid]:
                    per_role[role] += 1

                if is_tribal and tribal_type:
                    subtypes = (
                        c["type_line"].split("—", 1)[-1]
                        if "—" in c["type_line"]
                        else ""
                    ).lower()
                    if tribal_type in subtypes or tribal_type + "s" in subtypes:
                        tribal_count += 1

            if spell_slots == 0:
                continue

            # Register in buckets
            buckets = [
                "global",
                f"color_{color_count}",
                f"archetype_{archetype}",
                f"cmd_type_{commander_type}",
            ]
            for bucket in buckets:
                for role in REPORT_ROLES:
                    role_counts[bucket][role].append(float(per_role.get(role, 0)))

            if is_tribal and tribal_type:
                tribal_fracs[archetype].append(tribal_count / spell_slots)

            deck_log.append({
                "commander":      commander_name,
                "archetype":      archetype,
                "color_count":    color_count,
                "spell_slots":    spell_slots,
                "roles":          dict(per_role),
                "commander_type": commander_type,
            })

        log.info("Analysed %d decks", len(deck_log))

        # ── 5. Build profile JSON ─────────────────────────────────────────────

        def _stratum_summary(bucket: str) -> dict:
            return {role: _summary(role_counts[bucket][role]) for role in REPORT_ROLES}

        def _stratum_medians(bucket: str) -> dict[str, int]:
            return {role: _median_int(role_counts[bucket][role]) for role in REPORT_ROLES}

        color_keys    = sorted(k for k in role_counts if k.startswith("color_"))
        archetype_keys = sorted(k for k in role_counts if k.startswith("archetype_"))
        cmd_type_keys  = sorted(k for k in role_counts if k.startswith("cmd_type_"))

        profile: dict[str, Any] = {
            "_meta": {
                "deck_count":    len(deck_log),
                "generated_at":  datetime.now(timezone.utc).isoformat(),
                "roles_tracked": REPORT_ROLES,
                "notes": (
                    "Counts are per non-land spell slot.  Each card counts once per "
                    "bucket-level role even if it matches multiple fine-grained patterns "
                    "(e.g. a card matching both draw_one and repeatable_draw counts as 1 draw). "
                    "'evasion' counts cards with Flying/Menace/Shadow/etc. as a permanent keyword, "
                    "not temporary grants. "
                    "Outlier note: cEDH and spike decks skew toward higher ramp and lower removal; "
                    "consider filtering by archetype != 'combo' for casual-play targets."
                ),
            },

            # ── Drop-in replacement targets for generate.py ───────────────────
            # Integer medians across all decks.  Replace RAMP_TARGET etc. with
            # a lookup into this dict keyed by the commander's detected archetype.
            "targets": {
                "global": _stratum_medians("global"),
                **{
                    k.removeprefix("archetype_"): _stratum_medians(k)
                    for k in archetype_keys
                },
            },

            # ── Full percentile breakdown ─────────────────────────────────────
            "details": {
                "global": _stratum_summary("global"),
                "by_color_count": {
                    k.removeprefix("color_"): _stratum_summary(k)
                    for k in color_keys
                },
                "by_archetype": {
                    k.removeprefix("archetype_"): _stratum_summary(k)
                    for k in archetype_keys
                },
                "by_commander_type": {
                    k.removeprefix("cmd_type_"): _stratum_summary(k)
                    for k in cmd_type_keys
                },
            },

            # ── Tribal composition ────────────────────────────────────────────
            "tribal_composition": {
                arch: {
                    "n":    len(fracs),
                    "p50_tribal_fraction": round(_pct(fracs, 0.50), 3),
                    "mean_tribal_fraction": round(sum(fracs) / len(fracs), 3),
                    "p25":  round(_pct(fracs, 0.25), 3),
                    "p75":  round(_pct(fracs, 0.75), 3),
                    "note": (
                        "Fraction of non-land spell slots whose type line "
                        "contains the tribal creature type."
                    ),
                }
                for arch, fracs in sorted(tribal_fracs.items())
                if fracs
            },
        }

        # ── 6. Write output ───────────────────────────────────────────────────
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_FILE.write_text(json.dumps(profile, indent=2))
        log.info("Wrote profile → %s", OUTPUT_FILE)

        # ── 7. Human-readable console summary ────────────────────────────────
        g = profile["details"]["global"]
        n_decks = len(deck_log)

        print(f"\n{'=' * 55}")
        print(f"  Deck Composition Profile  ({n_decks} decks)")
        print(f"{'=' * 55}")
        print(f"\n  {'Role':<20} {'p25':>5} {'p50':>5} {'p75':>5}  {'mean':>5}")
        print(f"  {'-' * 43}")
        for role in REPORT_ROLES:
            s = g[role]
            print(f"  {role:<20} {s['p25']:>5} {s['p50']:>5} {s['p75']:>5}  {s['mean']:>5}")

        print(f"\n  {'--- By archetype (p50 medians, n≥3 shown) ---'}")
        print(f"  {'Archetype':<22}  {'n':>4}  {'ramp':>4}  {'draw':>4}  {'removal':>7}  {'tutor':>5}")
        for k in archetype_keys:
            arch = k.removeprefix("archetype_")
            d = profile["details"]["by_archetype"][arch]
            n_arch = d["ramp"]["n"]
            if n_arch < 3:
                continue
            print(
                f"  {arch:<22}  {n_arch:>4}  "
                f"{d['ramp']['p50']:>4}  {d['draw']['p50']:>4}  "
                f"{d['removal']['p50']:>7}  {d['tutor']['p50']:>5}"
            )

        print(f"\n  {'--- By color count (p50 medians) ---'}")
        print(f"  {'Colors':<8}  {'n':>4}  {'ramp':>4}  {'draw':>4}  {'removal':>7}")
        for k in color_keys:
            cc = k.removeprefix("color_")
            d = profile["details"]["by_color_count"][cc]
            n_cc = d["ramp"]["n"]
            print(
                f"  {cc + ' color(s)':<8}  {n_cc:>4}  "
                f"{d['ramp']['p50']:>4}  {d['draw']['p50']:>4}  "
                f"{d['removal']['p50']:>7}"
            )

        if profile["tribal_composition"]:
            print(f"\n  {'--- Tribal spell-slot fractions (p50) ---'}")
            for arch, data in profile["tribal_composition"].items():
                print(
                    f"  {arch:<28}  n={data['n']:>3}  "
                    f"p50={data['p50_tribal_fraction']:.1%}  "
                    f"mean={data['mean_tribal_fraction']:.1%}"
                )

        print(f"\n  Profile written to: {OUTPUT_FILE}")
        print(
            "\n  Recommendation: load this file at API startup from the same\n"
            "  Docker volume that holds the model checkpoint, and pass the\n"
            "  per-archetype targets dict to the iterative heuristic loop\n"
            "  (issue #61).  Rebuild by re-running this script after new\n"
            "  decklists are imported.\n"
        )

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
