"""Universal Commander staple categories — color-identity-gated card sets.

Each sub-module defines:
  SQL  : str    — SQL WHERE body selecting eligible cards for that category
  RATE : float  — fraction of MAX_POSITIVES to sample per commander (caps
                  the staple contribution to prevent representation collapse)

STAPLE_CATEGORIES aggregates the active categories into a single mapping:
  {category_name: (sql_where_body, effective_inclusion_rate)}

Effective rates are the base RATE for each module multiplied by
INCLUSION_FACTOR — a global scalar for dialling down all shared signal at
once if representation collapse is observed during training.

Usage in export_dataset_commanders.py:
  from synergy.staples import STAPLE_CATEGORIES
  for category, (where, rate) in STAPLE_CATEGORIES.items():
      cur.execute(f"SELECT id::text FROM cards WHERE {where}")
      ...

The caller is responsible for:
  1. Applying color-identity filtering (card_ci ⊆ commander_ci) in Python
  2. Sampling min(len(eligible), round(rate * MAX_POSITIVES)) cards per
     commander to prevent any one category from dominating
  3. Not adding category names to the archetype/key_list (staples should
     not influence the archetype field in the training artifact)

Environment variables
---------------------
  STAPLES_INCLUSION_FACTOR   float in (0, 1]  default 1.0
      Global multiplier applied to every category's RATE.  Reduce below 1.0
      to shrink the total staple footprint when collapse is observed.
      Example: 0.5 halves every category cap, leaving more room for
      mechanic-specific signal.

  STAPLES_INCLUDE_LANDS      "true" | "false"  default "true"
      When "false", manabase and utilityland are excluded entirely.
      Useful if land-identity overlap across commanders proves to be the
      primary driver of collapse.

Inclusion rates (at INCLUSION_FACTOR=1.0, INCLUDE_LANDS=true) reflect a
real 99-card Commander deck infrastructure split:

  mana_rocks      0.05  → 15 cards  (artifact mana producers)
  land_ramp       0.04  → 12 cards  (non-creature land-fetch spells)
  mana_dorks      0.03  →  9 cards  (creature mana producers)
  removal_exile   0.03  →  9 cards  (exile target — bypasses graveyard)
  removal_destroy 0.04  → 12 cards  (destroy target — fires death triggers)
  removal_damage  0.03  →  9 cards  (targeted damage / -X/-X)
  removal_bounce  0.02  →  6 cards  (return target to hand)
  sweeper         0.06  → 18 cards  (board wipes)
  draw_engine     0.08  → 24 cards  (repeatable draw permanents)
  draw_spell      0.06  → 18 cards  (one-shot draw instants/sorceries)
  interaction     0.06  → 18 cards  (counterspells + protection)
  manabase        0.16  → 48 cards  (colored-mana lands)
  utilityland     0.06  → 18 cards  (fetch lands + utility lands)
  ──────────────────────────────────
  total           0.72  → 216 / 300 MAX_POSITIVES
"""

from __future__ import annotations

import os

from mtg_sql.staples import (
    removal, sweeper, draw_engine, draw_spell, interaction,
    mana_rocks, mana_dorks, land_ramp,
)

from . import manabase, utilityland

# ── Levers ────────────────────────────────────────────────────────────────────

INCLUSION_FACTOR: float = float(os.environ.get("STAPLES_INCLUSION_FACTOR", "1.0"))
INCLUDE_LANDS: bool = os.environ.get("STAPLES_INCLUDE_LANDS", "true").lower() != "false"

# ── Category registry ─────────────────────────────────────────────────────────
#
# Ramp is split into three sub-bins so mana rocks, land ramp, and mana dorks
# cluster separately — Sol Ring should not be a positive peer of Llanowar Elves.
#
# Removal is split into four sub-bins matching the MTG rules distinction:
#   removal_exile   — bypasses graveyard; death triggers do NOT fire
#   removal_destroy — creature goes to graveyard; death triggers fire
#   removal_damage  — targeted damage / -X/-X; death triggers fire
#   removal_bounce  — soft removal; creature returns to hand
# These sub-bins must stay separate so that death-trigger commanders (Syr Konrad,
# Teysa) receive only destroy/damage removal as positives, not exile/bounce.
# Rates within each group sum to the original combined rate (ramp=0.12, removal=0.12).

_BASE: dict[str, tuple[str, float]] = {
    "mana_rocks":      (mana_rocks.SQL,    0.05),
    "land_ramp":       (land_ramp.SQL,     0.04),
    "mana_dorks":      (mana_dorks.SQL,    0.03),
    "removal_exile":   (removal.EXILE,     0.03),
    "removal_destroy": (removal.DESTROY,   0.04),
    "removal_damage":  (removal.DAMAGE,    0.03),
    "removal_bounce":  (removal.BOUNCE,    0.02),
    "sweeper":         (sweeper.SQL,       sweeper.RATE),
    "draw_engine":     (draw_engine.SQL,   draw_engine.RATE),
    "draw_spell":      (draw_spell.SQL,    draw_spell.RATE),
    "interaction":     (interaction.SQL,   interaction.RATE),
    "manabase":        (manabase.SQL,      manabase.RATE),
    "utilityland":     (utilityland.SQL,   utilityland.RATE),
}

STAPLE_CATEGORIES: dict[str, tuple[str, float]] = {
    category: (where, rate * INCLUSION_FACTOR)
    for category, (where, rate) in _BASE.items()
    if INCLUDE_LANDS or category not in ("manabase", "utilityland")
}

__all__ = ["STAPLE_CATEGORIES", "INCLUSION_FACTOR", "INCLUDE_LANDS"]
