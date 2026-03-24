"""Shared database connection and pipeline-wide constants.

All stage modules import from here so the engine is created once and constants
are defined in a single place.
"""
from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

DATABASE_URL = os.environ["DATABASE_URL"]
engine = create_async_engine(DATABASE_URL, echo=False)
Session = async_sessionmaker(engine, expire_on_commit=False)

# ── Embedding ─────────────────────────────────────────────────────────────────
EMBEDDING_MODEL = os.environ.get(
    "EMBEDDING_MODEL", "sentence-transformers/all-mpnet-base-v2"
)
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "256"))

# ── Synergy chunking — used by both dataset and commander stages ──────────────
SYNERGY_CHUNK = 200  # producers per transaction (~200 × consumers rows per commit)
SYNERGY_LIMIT = int(os.environ.get("SYNERGY_LIMIT", "500000"))  # max edges per trigger_event
