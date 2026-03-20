"""
Shared helpers for spike scripts.

Loads .env from the repo root automatically so scripts can be run
directly (python spike/foo.py) without going through the PowerShell
runner.  Call load_env() at import time or explicitly before reading
os.environ.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Repo root is two levels up from this file (spike/common.py → repo root)
_REPO_ROOT = Path(__file__).resolve().parent.parent


def load_env() -> None:
    """
    Parse .env in the repo root and inject missing vars into os.environ.
    Already-set variables are not overwritten (env takes priority over .env).
    """
    env_file = _REPO_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


# Run at import time so DATABASE_URL etc. are available as soon as any
# spike script does `import common` or `from common import ...`
load_env()


def _hf_login() -> None:
    """Authenticate with the HF Hub if HF_TOKEN is set."""
    token = os.environ.get("HF_TOKEN", "")
    if not token:
        return
    try:
        from huggingface_hub import login
        login(token=token, add_to_git_credential=False)
    except Exception:
        pass  # huggingface_hub not installed — skip silently


_hf_login()


def get_database_url(async_driver: bool = False) -> str:
    """
    Return DATABASE_URL, constructing it from POSTGRES_* vars if needed.
    Raises SystemExit with a clear message if nothing is configured.
    """
    url = os.environ.get("DATABASE_URL", "")

    if not url:
        user = os.environ.get("POSTGRES_USER", "")
        pw   = os.environ.get("POSTGRES_PASSWORD", "")
        db   = os.environ.get("POSTGRES_DB", "")
        if user and pw and db:
            scheme = "postgresql+asyncpg" if async_driver else "postgresql"
            url = f"{scheme}://{user}:{pw}@localhost:5432/{db}"
        else:
            sys.exit(
                "ERROR: DATABASE_URL is not set and POSTGRES_USER / "
                "POSTGRES_PASSWORD / POSTGRES_DB are incomplete.\n"
                "Copy .env.example to .env and fill in your credentials."
            )

    # Normalise the driver prefix
    if async_driver and url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if not async_driver and url.startswith("postgresql+asyncpg://"):
        url = url.replace("postgresql+asyncpg://", "postgresql://", 1)

    return url
