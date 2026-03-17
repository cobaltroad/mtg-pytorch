"""
Training trigger operations.

Uses the Docker SDK to launch trainer containers on demand.  The API container
must have /var/run/docker.sock mounted (see docker-compose.yml).
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

# These are set from the environment at API startup; training containers
# inherit them so they connect to the same DB and checkpoint volume.
_DATABASE_URL   = os.environ.get("DATABASE_URL", "")
_WANDB_API_KEY  = os.environ.get("WANDB_API_KEY", "")
_WANDB_PROJECT  = os.environ.get("WANDB_PROJECT", "edh-builder")
_COMPOSE_PROJECT = os.environ.get("COMPOSE_PROJECT_NAME", "mtg-pytorch")


def _sync_db_url(url: str) -> str:
    """Convert asyncpg DSN to psycopg2 DSN for the trainer."""
    return url.replace("postgresql+asyncpg://", "postgresql://")


def _get_client():
    import docker
    return docker.from_env()


def _trainer_env() -> dict[str, str]:
    return {
        "DATABASE_URL":   _sync_db_url(_DATABASE_URL),
        "CHECKPOINT_DIR": "/checkpoints",
        "WANDB_API_KEY":  _WANDB_API_KEY,
        "WANDB_PROJECT":  _WANDB_PROJECT,
    }


def _trainer_volumes() -> dict[str, dict]:
    return {
        f"{_COMPOSE_PROJECT}_model_checkpoints": {
            "bind": "/checkpoints",
            "mode": "rw",
        },
    }


def _trainer_network() -> str:
    return f"{_COMPOSE_PROJECT}_internal"


def start_training(
    phase: int,
    epochs: int,
    lr: float = 1e-4,
    resume: bool = True,
    freeze_encoder: bool = True,
    encoder_lr_scale: float = 0.1,
    temp_start: float = 0.5,
    temp_end: float = 0.05,
    sample: int = 500_000,
    role_demand_sample: int = 100_000,
) -> dict[str, Any]:
    """
    Launch a trainer container in detached mode.

    Returns {"container_id": str, "name": str, "status": str}
    or {"error": str} on failure.
    """
    cmd = ["python", "train.py",
           "--phase", str(phase),
           "--epochs", str(epochs),
           "--lr", str(lr)]

    if resume:
        cmd.append("--resume")

    if phase == 2:
        cmd += ["--sample", str(sample),
                "--role-demand-sample", str(role_demand_sample)]

    if phase == 4:
        if not freeze_encoder:
            cmd.append("--no-freeze-encoder")
            cmd += ["--encoder-lr-scale", str(encoder_lr_scale)]
        cmd += ["--temp-start", str(temp_start), "--temp-end", str(temp_end)]

    try:
        client = _get_client()
        container = client.containers.run(
            image=f"{_COMPOSE_PROJECT}-trainer:latest",
            command=cmd,
            environment=_trainer_env(),
            volumes=_trainer_volumes(),
            network=_trainer_network(),
            detach=True,
            labels={
                "mtg.trainer.phase": str(phase),
                "mtg.trainer.managed": "true",
            },
        )
        log.info("Started trainer container %s (phase %d)", container.short_id, phase)
        return {
            "container_id": container.id,
            "short_id":     container.short_id,
            "name":         container.name,
            "status":       container.status,
            "command":      " ".join(cmd),
        }
    except Exception as exc:
        log.error("Failed to start trainer: %s", exc)
        return {"error": str(exc)}


def list_training_runs() -> list[dict[str, Any]]:
    """Return all trainer containers (running or recently exited)."""
    try:
        client = _get_client()
        containers = client.containers.list(
            all=True,
            filters={"label": "mtg.trainer.managed=true"},
        )
        result = []
        for c in containers:
            result.append({
                "container_id": c.id,
                "short_id":     c.short_id,
                "name":         c.name,
                "status":       c.status,
                "phase":        c.labels.get("mtg.trainer.phase", "?"),
                "started":      c.attrs.get("State", {}).get("StartedAt", ""),
                "finished":     c.attrs.get("State", {}).get("FinishedAt", ""),
                "exit_code":    c.attrs.get("State", {}).get("ExitCode"),
            })
        return sorted(result, key=lambda r: r["started"], reverse=True)
    except Exception as exc:
        log.error("Failed to list trainer containers: %s", exc)
        return [{"error": str(exc)}]


def get_logs(container_id: str, tail: int = 100) -> str:
    """Return the last `tail` lines of a container's logs."""
    try:
        client = _get_client()
        container = client.containers.get(container_id)
        return container.logs(tail=tail, timestamps=False).decode("utf-8", errors="replace")
    except Exception as exc:
        return f"Error fetching logs: {exc}"


def stop_training(container_id: str) -> dict[str, Any]:
    """Stop a running trainer container."""
    try:
        client = _get_client()
        container = client.containers.get(container_id)
        container.stop(timeout=10)
        return {"ok": True, "container_id": container_id, "status": container.status}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
