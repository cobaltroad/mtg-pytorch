"""Tests for the pure drift comparison in scripts/eval_harness.py (#137)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from scripts.eval_harness import signal_drift  # noqa: E402


def test_in_sync():
    live = {"c1": {"a", "b"}, "c2": {"x"}}
    assert signal_drift(live, {"c1": {"a", "b"}, "c2": {"x"}}) == {"missing": 0, "stale": 0}


def test_new_pattern_not_materialized():
    live = {"c1": {"a", "b"}}          # code now fires 'b'
    stored = {"c1": {"a"}}             # DB was built before 'b' existed
    assert signal_drift(live, stored) == {"missing": 1, "stale": 0}


def test_removed_pattern_lingers():
    live = {"c1": {"a"}}
    stored = {"c1": {"a", "old_key"}}  # removed pattern still in DB
    assert signal_drift(live, stored) == {"missing": 0, "stale": 1}


def test_commander_absent_from_db_counts_as_missing():
    live = {"c1": {"a"}, "c_new": {"b"}}   # newly legal commander, never decomposed
    stored = {"c1": {"a"}}
    assert signal_drift(live, stored) == {"missing": 1, "stale": 0}


def test_zero_signal_commanders_do_not_drift():
    live = {"c1": set()}
    stored = {}
    assert signal_drift(live, stored) == {"missing": 0, "stale": 0}
