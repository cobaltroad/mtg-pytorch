"""Tests for _dedupe_by_oracle_id in stages/download.py.

MTGJSON AtomicCards contains duplicate entries for reversible printings
("Sol Ring // Sol Ring" alongside "Sol Ring", same scryfallOracleId).
The dedupe must collapse those without touching genuine two-face names.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from stages.download import _dedupe_by_oracle_id  # noqa: E402


def _card(name: str, oracle_id: str) -> dict:
    return {"name": name, "oracle_id": oracle_id}


def test_doubled_name_collapsed_and_deduped():
    cards = [
        _card("Sol Ring", "oid-1"),
        _card("Sol Ring // Sol Ring", "oid-1"),
    ]
    out = _dedupe_by_oracle_id(cards)
    assert len(out) == 1
    assert out[0]["name"] == "Sol Ring"


def test_doubled_entry_first_still_wins_single_name():
    cards = [
        _card("Command Tower // Command Tower", "oid-2"),
        _card("Command Tower", "oid-2"),
    ]
    out = _dedupe_by_oracle_id(cards)
    assert len(out) == 1
    assert out[0]["name"] == "Command Tower"


def test_doubled_name_without_single_twin_is_collapsed():
    out = _dedupe_by_oracle_id([_card("Propaganda // Propaganda", "oid-3")])
    assert len(out) == 1
    assert out[0]["name"] == "Propaganda"


def test_real_two_face_names_untouched():
    mdfc = _card("Malakir Rebirth // Malakir Mire", "oid-4")
    split = _card("Fire // Ice", "oid-5")
    out = _dedupe_by_oracle_id([mdfc, split])
    assert {c["name"] for c in out} == {"Malakir Rebirth // Malakir Mire", "Fire // Ice"}


def test_distinct_cards_never_merged():
    out = _dedupe_by_oracle_id([_card("Sol Ring", "oid-1"), _card("Arcane Signet", "oid-6")])
    assert len(out) == 2
