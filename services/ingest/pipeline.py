"""
MTG ingest pipeline -- thin orchestrator.

Two-step workflow
-----------------
download   -- Fetch card data (MTGJSON -> cards table) + import combos (Commander Spellbook).
              Re-run when new sets release or combo data changes.
process    -- Embed cards, tag abilities, compute synergy edges, export training artifact.
              Re-run after download or after model/pattern changes.

Commander artifact pipeline (run after process):
  python pipeline.py --stage compute_commander_value_synergy
  python pipeline.py --stage compute_tribal_typeline_synergy
  python pipeline.py --stage export_dataset_commanders

Run both:           python pipeline.py
Run download only:  python pipeline.py --stage download
Run process only:   python pipeline.py --stage process

Individual sub-stages (rarely needed):
  embed_cards, tag_abilities [--rescan],
  compute_synergy, compute_synergy_xmage, compute_effect_peer_synergy,
  compute_commander_value_synergy, compute_tribal_typeline_synergy,
  export_dataset, export_dataset_commanders

Data sources
------------
Primary:  MTGJSON bulk downloads (https://mtgjson.com/downloads/)
          No rate limits; full machine-readable dataset.
Fallback: Scryfall oracle_cards bulk JSON -- only used if MTGJSON unavailable,
          because Scryfall enforces strict rate limits on their API.
Combos:   Commander Spellbook API -- fetched during download step.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

from stages.download import download as _download          # noqa: E402
from stages.tag import embed_cards, tag_abilities          # noqa: E402
from stages.dataset import compute_synergy, compute_synergy_xmage, compute_effect_peer_synergy  # noqa: E402
from stages.commander import (                             # noqa: E402
    compute_commander_value_synergy,
    compute_tribal_typeline_synergy,
)
from stages.export import (                                # noqa: E402
    export_dataset_stage,
    export_dataset_commanders_stage,
    composition_profile_stage,
)


# -- Orchestrators ------------------------------------------------------------

async def download() -> None:
    """Fetch card data + combos and load into the database.

    Run this first (or whenever MTGJSON / Commander Spellbook has new data).
    """
    await _download()


async def process() -> None:
    """Embed -> tag -> compute synergy paths -> export training dataset.

    Requires the download step to have been run first.

    Note: commander-specific synergy edges (compute_commander_value_synergy,
    compute_tribal_typeline_synergy) are NOT included here -- they are
    prerequisites for export_dataset_commanders only and should be run
    explicitly before building the commander artifact.
    """
    await embed_cards()
    await tag_abilities()
    await compute_synergy()
    await compute_synergy_xmage()
    await compute_effect_peer_synergy()
    export_dataset_stage()
    await composition_profile_stage()


async def run_all() -> None:
    """Full pipeline: download + process."""
    await download()
    await process()


# -- CLI ----------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stage",
        choices=[
            # Grouped stages
            "download", "process",
            # Tag sub-stages
            "embed_cards", "tag_abilities", "tag_abilities_xmage",
            # Synergy sub-stages
            "compute_synergy", "compute_synergy_xmage", "compute_effect_peer_synergy",
            # Commander synergy (prerequisites for export_dataset_commanders)
            "compute_commander_value_synergy",
            "compute_tribal_typeline_synergy",
            # Export sub-stages
            "export_dataset", "export_dataset_commanders", "composition_profile",
        ],
        default=None,
        help=(
            "download: fetch MTGJSON + load cards + import combos. "
            "process: embed + tag + compute_synergy + compute_synergy_xmage + compute_effect_peer_synergy + export_dataset. "
            "compute_commander_value_synergy / compute_tribal_typeline_synergy: "
            "  run these (plus compute_synergy if not done) before export_dataset_commanders. "
            "tag_abilities_xmage: supplement card_abilities from XMage source tree "
            "(requires XMAGE_DIR env var; mount mage/ read-only). "
            "Omit to run both download and process."
        ),
    )
    parser.add_argument(
        "--rescan",
        action="store_true",
        help=(
            "tag_abilities only: re-apply every trigger pattern to every card, "
            "not just those with 0 existing rows.  Use after improving a pattern regex."
        ),
    )
    args = parser.parse_args()

    if args.stage == "download":
        asyncio.run(download())
    elif args.stage == "process":
        asyncio.run(process())
    elif args.stage == "embed_cards":
        asyncio.run(embed_cards())
    elif args.stage == "tag_abilities":
        asyncio.run(tag_abilities(rescan=args.rescan))
    elif args.stage == "tag_abilities_xmage":
        from xmage_parse import tag_abilities_xmage as _xmage_tag
        import os as _os
        from pathlib import Path as _Path
        asyncio.run(_xmage_tag(_Path(_os.environ.get("XMAGE_DIR", "/mage"))))
    elif args.stage == "compute_synergy":
        asyncio.run(compute_synergy())
    elif args.stage == "compute_synergy_xmage":
        asyncio.run(compute_synergy_xmage())
    elif args.stage == "compute_effect_peer_synergy":
        asyncio.run(compute_effect_peer_synergy())
    elif args.stage == "compute_commander_value_synergy":
        asyncio.run(compute_commander_value_synergy())
    elif args.stage == "compute_tribal_typeline_synergy":
        asyncio.run(compute_tribal_typeline_synergy())
    elif args.stage == "export_dataset":
        export_dataset_stage()
    elif args.stage == "export_dataset_commanders":
        export_dataset_commanders_stage()
    elif args.stage == "composition_profile":
        asyncio.run(composition_profile_stage())
    else:
        asyncio.run(run_all())
