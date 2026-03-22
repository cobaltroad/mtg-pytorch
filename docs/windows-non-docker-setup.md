# Windows GPU Setup (No Docker / No WSL)

This is the permanent training environment.  The GPU machine runs natively on
Windows using a Python `.venv`, a remote PostgreSQL connection to the Docker
host, and PowerShell scripts.  There is no Docker on the GPU machine.

See `CLAUDE.md → Two-environment setup` for the full picture of how this
machine relates to the Docker host.

---

## 1) Prerequisites

- Windows 11 (or recent Windows 10)
- Python 3.12 installed
- PostgreSQL installed and running on `localhost:5432`
- Project repo checked out

Optional:
- `psql` / `pg_restore` in PATH (for dump restore)

---

## 2) Python environment

From repo root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv\Scripts\python.exe -m pip install -r services\api\requirements.txt -r services\ingest\requirements.txt -r services\trainer\requirements.txt -r services\ui\requirements.txt
```

---

## 3) Environment config

Create `.env` (already ignored by git) and set at minimum:

- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- optional `WANDB_API_KEY`

If `DATABASE_URL` is not explicitly set, local scripts derive it from those values.

---

## 4) Restore database dump

### Custom dump (`.dump`, `.backup`, `.tar`)

```powershell
$env:PGPASSWORD="<postgres_password>"
createdb -h localhost -p 5432 -U mtg mtg
pg_restore -h localhost -p 5432 -U mtg -d mtg --clean --if-exists --no-owner --no-privileges .\db_main.dump
```

### Plain SQL dump (`.sql`)

```powershell
$env:PGPASSWORD="<postgres_password>"
createdb -h localhost -p 5432 -U mtg mtg
psql -h localhost -p 5432 -U mtg -d mtg -f .\db_main.sql
```

Ensure extension exists:

```powershell
psql -h localhost -p 5432 -U mtg -d mtg -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

---

## 5) Restore cached artifacts (optional but recommended)

### Ingest cache

```powershell
New-Item -ItemType Directory -Force -Path .\ingest_cache | Out-Null
tar -xzf .\ingest_cache.tar.gz -C .\ingest_cache
```

### Model checkpoints

```powershell
New-Item -ItemType Directory -Force -Path .\checkpoints | Out-Null
tar -xzf .\model_checkpoints.tar.gz -C .\checkpoints
```

Notes:
- `scripts\start-api.ps1` uses `MODEL_CHECKPOINT_DIR=<repo>\checkpoints` by default.
- `scripts\run-local-job.ps1` uses `CHECKPOINT_DIR=<repo>\checkpoints` for training.

---

## 6) Start API and UI locally

### Terminal 1: API

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-api.ps1
```

### Terminal 2: UI

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-ui.ps1
```

URLs:
- API docs: `http://localhost:8000/docs`
- UI: `http://localhost:8501`

---

## 7) One-command local jobs (train or ingest)

Use `scripts\run-local-job.ps1`.

### A) Train by phase (mirrors Re-Train tab)

Phase 2 (ability-trigger synergy):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run.ps1 -Mode train -Phase 2
```

Phase 3 (deck co-occurrence):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run.ps1 -Mode train -Phase 3
```

Phase 4 (deck constructor):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run.ps1 -Mode train -Phase 4
```

Example with overrides:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run.ps1 -Mode train -Phase 4 -Epochs 50 -LearningRate 1e-4 -Resume:$false -FreezeEncoder:$false -EncoderLrScale 0.1 -TempStart 0.5 -TempEnd 0.05
```

### B) Run ingest stage(s)

Synergy-only rebuild:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-local-job.ps1 -Mode ingest -Stage compute_synergy -SynergyLimit 500000
powershell -ExecutionPolicy Bypass -File .\scripts\run-local-job.ps1 -Mode ingest -Stage compute_tribal_typeline_synergy -TribalMemberLimit 50000
```

Run full ingest pipeline:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-local-job.ps1 -Mode ingest -Stage all
```

Available stages:
- `fetch_cards`
- `load_cards`
- `embed_cards`
- `tag_abilities`
- `compute_synergy`
- `compute_tribal_typeline_synergy`
- `all`

---

## 8) Quick health checks

```powershell
Invoke-WebRequest http://localhost:8000/health | Select-Object -Expand Content
```

```powershell
psql -h localhost -p 5432 -U mtg -d mtg -c "SELECT COUNT(*) FROM cards;"
psql -h localhost -p 5432 -U mtg -d mtg -c "SELECT COUNT(*) FROM synergy_edges;"
```

---

## 9) Troubleshooting

- `DATABASE_URL not configured`
  - Ensure `.env` exists and has DB values or set `DATABASE_URL` manually.

- `connection refused localhost:5432`
  - PostgreSQL service is down or bound to another port.

- UI starts but training buttons fail
  - Re-Train tab uses API Docker-trigger endpoint; in this no-Docker setup use `scripts\run-local-job.ps1` for training.

- Missing model checkpoints
  - Extract `model_checkpoints.tar.gz` into `checkpoints/` or train from scratch.

---

## 10) GPU setup

The GPU machine is now active. PyTorch with CUDA support is required for training.

### Install CUDA-capable PyTorch

Replace the CPU-only PyTorch that may have been installed via `requirements.txt`:

```powershell
.\.venv\Scripts\pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
```

Adjust `cu121` to match your installed CUDA version (`cu118`, `cu124`, etc.).
Verify: `.\.venv\Scripts\python -c "import torch; print(torch.cuda.is_available())"` should print `True`.

### Training on GPU

No changes to the training commands — PyTorch detects CUDA automatically.
The trainer moves tensors to `cuda` when available:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run.ps1 -Mode train -Phase 4 -Epochs 50 -LearningRate 1e-4
```

### Checkpoint compatibility

Checkpoints are saved with `torch.save` and are CPU/GPU agnostic — the API
loads them with `map_location=cpu`, so a checkpoint trained on GPU uploads
and runs on the Docker host without conversion.

### Recommended presets by phase

| Phase | Key flags | Notes |
|-------|-----------|-------|
| 1 | default | Fast; contrastive on reprints |
| 2 | `--sample 500000 --neg-ratio 3` | Reduce if OOM |
| 3 | `--epochs 50 --lr 1e-4` | More decks = better loss |
| 4 | `--epochs 50 --lr 1e-4 --encoder-lr-scale 0.1 --temp-start 0.5 --temp-end 0.05` | Freeze encoder by default |

