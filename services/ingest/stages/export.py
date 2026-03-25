"""Export stage — serialize training artifacts and support files.

All functions here are thin wrappers that delegate to the dedicated
export modules so pipeline.py stays free of artifact-specific logic.

Artifacts:
  mtg_dataset.pt      — Phases 1-2 (text equivalence + ability-trigger synergy)
  mtg_commanders.pt   — Phases 3-4 (commander BPR from synergy_edges)
  deck_composition_profile.json — structural targets for deck generation

Entrypoint:  python -m stages.export
             [--stage export_dataset|export_dataset_commanders|composition_profile]
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
      stages.dataset   — compute_synergy (ability_trigger edges)
      stages.commander — compute_commander_value_synergy + compute_tribal_typeline_synergy
    """
    import export_dataset_commanders
    export_dataset_commanders.main()


async def composition_profile_stage() -> None:
    """Rebuild /data/deck_composition_profile.json from the imported deck pool.

    Always regenerates — call after importing new decklists or after a full
    process run so the API's structural targets stay current.
    """
    import deck_composition_profile as dcp
    log.info("Regenerating deck composition profile → %s", dcp.OUTPUT_FILE)
    await dcp.main()


if __name__ == "__main__":
    import argparse
    import asyncio
    import logging as _logging

    _logging.basicConfig(level=_logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Export training artifacts")
    parser.add_argument(
        "--stage",
        choices=[
            "export_dataset",
            "export_dataset_commanders",
            "composition_profile",
        ],
        default=None,
        help="Export a specific artifact (default: export all)",
    )
    args = parser.parse_args()

    if args.stage == "export_dataset":
        export_dataset_stage()
    elif args.stage == "export_dataset_commanders":
        export_dataset_commanders_stage()
    elif args.stage == "composition_profile":
        asyncio.run(composition_profile_stage())
    else:
        export_dataset_stage()
        export_dataset_commanders_stage()
        asyncio.run(composition_profile_stage())
