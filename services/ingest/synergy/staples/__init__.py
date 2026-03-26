"""Universal Commander staple categories — color-identity-gated card sets.

Each sub-module defines:
  SQL  : str    — SQL WHERE body selecting eligible cards for that category
  RATE : float  — fraction of MAX_POSITIVES to sample per commander (caps
                  the staple contribution to prevent representation collapse)

STAPLE_CATEGORIES aggregates all eight categories into a single mapping:
  {category_name: (sql_where_body, inclusion_rate)}

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

Inclusion rates are calibrated to broadly reflect a real 99-card Commander
deck infrastructure split (72 cards total across all categories, leaving
~28 slots for mechanic-specific signal):

  ramp          0.12  → 36 cards  (mana rocks + land ramp + dorks)
  removal       0.12  → 36 cards  (targeted destroy/exile/bounce/-X/-X)
  sweeper       0.06  → 18 cards  (board wipes)
  draw_engine   0.08  → 24 cards  (repeatable draw permanents)
  draw_spell    0.06  → 18 cards  (one-shot draw instants/sorceries)
  interaction   0.06  → 18 cards  (counterspells + protection)
  manabase      0.16  → 48 cards  (colored-mana lands)
  utilityland   0.06  → 18 cards  (fetch lands + utility lands)
  ─────────────────────────────────
  total         0.72  → 216 / 300 MAX_POSITIVES
"""

from __future__ import annotations

from . import (
    ramp,
    removal,
    sweeper,
    draw_engine,
    draw_spell,
    interaction,
    manabase,
    utilityland,
)

STAPLE_CATEGORIES: dict[str, tuple[str, float]] = {
    "ramp":        (ramp.SQL,        ramp.RATE),
    "removal":     (removal.SQL,     removal.RATE),
    "sweeper":     (sweeper.SQL,     sweeper.RATE),
    "draw_engine": (draw_engine.SQL, draw_engine.RATE),
    "draw_spell":  (draw_spell.SQL,  draw_spell.RATE),
    "interaction": (interaction.SQL, interaction.RATE),
    "manabase":    (manabase.SQL,    manabase.RATE),
    "utilityland": (utilityland.SQL, utilityland.RATE),
}

__all__ = ["STAPLE_CATEGORIES"]
