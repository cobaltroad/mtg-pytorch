"""Tests for scripts/report_slot_disputes.py (#182) — pure parts only.

The DB-facing queries are exercised live; here we pin the invariants that
keep the report trustworthy as pool SQL evolves:
  * every named mode is a genuine sub-fragment of the slot's POOL_SQL
    (a mode that drifts out of the real pool would misattribute matches)
  * every pool-drawn slot the builder can emit is classifiable
  * every mode has a fix location
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
for _parent in Path(__file__).resolve().parents:
    if (_parent / "shared" / "composition").is_dir():
        sys.path.insert(0, str(_parent / "shared"))
        break

from composition.pool_helpers import POOL_SQL  # noqa: E402
from scripts.report_slot_disputes import FIX_LOCATION, SLOT_MODES  # noqa: E402


def test_modes_are_subfragments_of_pool_sql():
    for slot, modes in SLOT_MODES.items():
        pool = POOL_SQL[slot]
        for name, fragment in modes.items():
            assert fragment in pool, f"{name} drifted out of POOL_SQL[{slot!r}]"


def test_all_pool_slots_covered():
    # 'drain' is a density audit, never a slot the builder assigns.
    assert set(SLOT_MODES) == set(POOL_SQL) - {"drain"}


def test_every_mode_has_fix_location():
    for modes in SLOT_MODES.values():
        for name in modes:
            assert name in FIX_LOCATION, f"no fix location for {name}"
    assert "theme" in FIX_LOCATION and "forced" in FIX_LOCATION
