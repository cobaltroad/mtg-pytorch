"""Composition-first deck builder — shared pure-Python layer.

Layer 1 of docs/composition-first-plan.md.  No DB, no torch: importable
from ingest (PYTHONPATH=/shared), the API, and the Windows trainer.

Modules
-------
* :mod:`card_facts` — mana-cost pip parsing and land classification.
  Persisted to the ``card_facts`` table by
  ``pipeline.py --stage compute_card_facts``.
"""

from .card_facts import (  # noqa: F401
    CardFacts,
    LandFacts,
    ManaProfile,
    classify_land,
    compute_card_facts,
    is_mdfc_land,
    parse_mana_cost,
)
