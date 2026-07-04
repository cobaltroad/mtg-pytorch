"""Export stage — serialize training artifacts and support files.

All functions here are thin wrappers that delegate to the dedicated
export modules so pipeline.py stays free of artifact-specific logic.

Artifacts:
  mtg_dataset.pt      — Phases 1-2 (text equivalence + ability-trigger synergy)
  mtg_commanders.pt   — Phases 3-4 (commander BPR from synergy_edges)

Entrypoint:  python -m stages.export
             [--stage export_dataset|export_dataset_commanders]
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def export_dataset_stage() -> None:
    """Serialize the training artifact to /data/mtg_dataset.pt."""
    import export_dataset
    export_dataset.main()


def export_dataset_commanders_stage() -> None:
    """Build the commander-decomposition artifact /data/mtg_commanders.pt.

    Prerequisites (must be run first):
      stages.dataset   — compute_textmatch_synergy (ability_trigger edges, tribal edges)
      stages.commander — compute_commander_value_synergy
    """
    import export_dataset_commanders
    export_dataset_commanders.main()


if __name__ == "__main__":
    import argparse
    import logging as _logging

    _logging.basicConfig(level=_logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Export training artifacts")
    parser.add_argument(
        "--stage",
        choices=[
            "export_dataset",
            "export_dataset_commanders",
        ],
        default=None,
        help="Export a specific artifact (default: export all)",
    )
    args = parser.parse_args()

    if args.stage == "export_dataset":
        export_dataset_stage()
    elif args.stage == "export_dataset_commanders":
        export_dataset_commanders_stage()
    else:
        export_dataset_stage()
        export_dataset_commanders_stage()
