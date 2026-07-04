"""Composition-first deck builder — shared pure-Python layer.

Layer 1 of docs/composition-first-plan.md.  No DB, no torch: importable
from ingest (PYTHONPATH=/shared), the API, and the Windows trainer.

Modules
-------
* :mod:`card_facts` — mana-cost pip parsing and land classification.
  Persisted to the ``card_facts`` table by
  ``pipeline.py --stage compute_card_facts``.
* :mod:`karsten`    — hypergeometric castability math (required colored
  sources per pip requirement and turn).
* :mod:`profile`    — CompositionProfile: deterministic quota derivation
  per commander, every value with a "because" rationale.
"""

from .karsten import castable_prob, required_sources  # noqa: F401
from .profile import CompositionProfile, derive_profile  # noqa: F401
from .card_facts import (  # noqa: F401
    CardFacts,
    LandFacts,
    ManaProfile,
    classify_land,
    compute_card_facts,
    is_mdfc_land,
    parse_mana_cost,
)
