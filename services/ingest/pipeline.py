"""
MTG ingest pipeline -- thin orchestrator.

Two-step workflow
-----------------
download   -- Fetch card data (MTGJSON -> cards table) + import combos (Commander Spellbook).
              Re-run when new sets release or combo data changes.
process    -- Embed cards, tag mechanic roles, compute synergy edges, export training artifact.
              Re-run after download or after model/pattern changes.

Commander artifact pipeline (run after process):
  python pipeline.py --stage decompose_commanders             Step 0:  write card_abilities rows (source='decompose')
  python pipeline.py --stage compute_commander_value_synergy  Step 1:  commander-value synergy edges
  python pipeline.py --stage export_dataset_commanders        Step 2:  export mtg_commanders.pt

Run both:           python pipeline.py
Run download only:  python pipeline.py --stage download
Run process only:   python pipeline.py --stage process

Individual sub-stages (rarely needed):
  embed_cards, tag_mechanic_tags [--rescan],
  compute_textmatch_synergy, compute_xmage_synergy, compute_xmage_effect_synergy,
  decompose_commanders, compute_commander_value_synergy,
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
import os
import sys

# Make scripts/ importable (export_dataset, import_*, etc. live there)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

from stages.download import download as _download  # noqa: E402
from stages.tag import embed_cards  # noqa: E402
from stages.mechanic_tags import tag_mechanic_tags  # noqa: E402
from stages.dataset import (
    compute_textmatch_synergy,
    compute_xmage_synergy,
    compute_xmage_effect_synergy,
)  # noqa: E402
from stages.export import (  # noqa: E402
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
    """Embed -> tag mechanic roles -> compute synergy paths -> export training dataset.

    Requires the download step to have been run first.

    Note: compute_commander_value_synergy is NOT included here -- it is a
    prerequisite for export_dataset_commanders only and should be run
    explicitly before building the commander artifact.
    """
    await embed_cards()
    tag_mechanic_tags()
    await compute_textmatch_synergy()
    await compute_xmage_synergy()
    await compute_xmage_effect_synergy()
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
            "download",
            "process",
            # Tag sub-stages
            "embed_cards",
            "tag_mechanic_tags",
            "tag_abilities_xmage",
            # Synergy sub-stages
            "compute_textmatch_synergy",
            "compute_xmage_synergy",
            "compute_xmage_effect_synergy",
            # Commander artifact sub-stages
            "decompose_commanders",
            "compute_commander_value_synergy",
            # Export sub-stages
            "export_dataset",
            "export_dataset_commanders",
            "composition_profile",
        ],
        default=None,
        help=(
            "download: fetch MTGJSON + load cards + import combos. "
            "process: embed + tag_mechanic_tags + compute synergy + export_dataset. "
            "tag_mechanic_tags: write deck-key role tags to candidate cards (source='mechanic'). "
            "tag_abilities_xmage: supplement card_abilities from XMage source tree "
            "(requires XMAGE_DIR env var; mount mage/ read-only). "
            "Omit to run both download and process."
        ),
    )
    parser.add_argument(
        "--rescan",
        action="store_true",
        help=(
            "tag_mechanic_tags only: delete all existing source='mechanic' role rows "
            "first, then re-insert.  Use after updating SQL in synergy modules."
        ),
    )
    args = parser.parse_args()

    if args.stage == "download":
        asyncio.run(download())
    elif args.stage == "process":
        asyncio.run(process())
    elif args.stage == "embed_cards":
        asyncio.run(embed_cards())
    elif args.stage == "tag_mechanic_tags":
        tag_mechanic_tags(rescan=args.rescan)
    elif args.stage == "tag_abilities_xmage":
        from xmage_parse import tag_abilities_xmage as _xmage_tag
        import os as _os
        from pathlib import Path as _Path

        asyncio.run(_xmage_tag(_Path(_os.environ.get("XMAGE_DIR", "/mage"))))
    elif args.stage == "compute_textmatch_synergy":
        asyncio.run(compute_textmatch_synergy())
    elif args.stage == "compute_xmage_synergy":
        asyncio.run(compute_xmage_synergy())
    elif args.stage == "compute_xmage_effect_synergy":
        asyncio.run(compute_xmage_effect_synergy())
    elif args.stage == "decompose_commanders":
        from stages.decompose import write_commander_abilities as _decompose

        _decompose()
    elif args.stage == "export_dataset":
        export_dataset_stage()
    elif args.stage == "export_dataset_commanders":
        export_dataset_commanders_stage()
    elif args.stage == "composition_profile":
        asyncio.run(composition_profile_stage())
    else:
        asyncio.run(run_all())
