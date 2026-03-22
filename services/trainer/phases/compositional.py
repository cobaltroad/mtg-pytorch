"""
Compositional reasoning training path — data loading stubs.

Supervision signal: functional equivalence from ability tags (Phase 1),
expanded oracle-text synergy patterns (Phase 2), commander role-matching
from oracle text (Phase 3), and role-gap sequencing (Phase 4).

See epic #71 and issues #65-#68 for implementation details.
Checkpoints are prefixed ``comp_phase``.
"""

from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)

CHECKPOINT_PREFIX = "comp_phase"


def warm_start_name(phase: int) -> str:
    """Return the checkpoint name to warm-start from for the given phase."""
    return {
        2: "comp_phase1_best",
        3: "comp_phase2_best",
        4: "comp_phase3_best",
    }.get(phase, "")


def load_synergy_pairs(
    embeddings: dict,
    neg_ratio: int = 3,
    sample: int = 500_000,
    hard_neg_frac: float = 0.5,
    role_demand_sample: int = 100_000,
    combo_sample: int = 200_000,
    commander_value_sample: int = 200_000,
) -> list[tuple[str, str, float]]:
    """Phase 2 compositional: expanded pattern library + XMage-derived pairs.

    Not yet implemented.  See #66.
    """
    raise NotImplementedError(
        "Phase 2 compositional data loading not yet implemented -- see issue #66"
    )


def load_decks(embeddings: dict[str, np.ndarray]) -> list[dict]:
    """Phase 3 compositional: commander role-matching triples.

    Not yet implemented.  See #67.
    """
    raise NotImplementedError(
        "Phase 3 compositional data loading not yet implemented -- see issue #67"
    )


def load_synergy_positions(
    decks: list[dict],
    embeddings: dict[str, np.ndarray],
    combo_weight: float = 3.0,
    ability_weight: float = 2.0,
    tribal_weight: float = 1.5,
    synergy_limit_per_commander: int = 300,
) -> list[dict]:
    """Phase 4 compositional: role-gap sequencing positions.

    Not yet implemented.  See #68.
    """
    raise NotImplementedError(
        "Phase 4 compositional synergy positions not yet implemented -- see issue #68"
    )


def load_synergy_positions_global(
    embeddings: dict[str, np.ndarray],
    ability_weight: float = 2.0,
    tribal_weight: float = 1.5,
    synergy_limit_per_commander: int = 300,
) -> list[dict]:
    """Phase 4 compositional: global role-gap positions (all legal commanders).

    Not yet implemented.  See #68.
    """
    raise NotImplementedError(
        "Phase 4 compositional global positions not yet implemented -- see issue #68"
    )
