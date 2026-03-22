"""Deck composition targets — data-driven slot counts per role.

Loaded once at module import from the profile JSON written by
``services/ingest/deck_composition_profile.py`` (issue #62).  Falls back
to hardcoded defaults if the file is absent or unreadable.

The profile is regenerated after importing new decklists:

    docker compose run --rm ingest python deck_composition_profile.py

The API must be restarted to pick up a freshly generated profile (the
file is loaded at import time, not per-request).

Override the file path via the ``DECK_COMPOSITION_PROFILE`` environment
variable (useful for tests).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

PROFILE_PATH = Path(
    os.environ.get("DECK_COMPOSITION_PROFILE", "/data/deck_composition_profile.json")
)

# Hardcoded fallbacks — global p50 estimates from deckbuilding heuristics.
# Updated when deck_composition_profile.py is run and the profile is loaded.
_DEFAULTS: dict[str, int] = {
    "ramp":        10,  # informational; ramp slots are pre-selected separately
    "removal":      8,
    "draw":         8,
    "evasion":      4,
    "tutor":        3,
    "protection":   4,
    "interaction":  3,
    "token":        4,
    "recursion":    3,
    "win_condition": 2,
}


def _load() -> dict[str, int]:
    if not PROFILE_PATH.exists():
        log.debug("Composition profile not found at %s; using defaults", PROFILE_PATH)
        return dict(_DEFAULTS)
    try:
        data    = json.loads(PROFILE_PATH.read_text())
        raw     = data.get("targets", {}).get("global", {})
        result  = dict(_DEFAULTS)
        for key in _DEFAULTS:
            if key in raw and isinstance(raw[key], (int, float)):
                result[key] = int(raw[key])
        log.info("Loaded deck composition targets from %s: %s", PROFILE_PATH, result)
        return result
    except Exception as exc:
        log.warning("Failed to load %s; using defaults: %s", PROFILE_PATH, exc)
        return dict(_DEFAULTS)


# Module-level singleton — loaded once at startup.
TARGETS: dict[str, int] = _load()
