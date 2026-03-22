# MTG Commander AI — Project Guide

## What this project is

A PyTorch system that trains a model to understand Magic: The Gathering card
interactions and ultimately build 99-card Commander decks given a single
commander card.  The goal is *model-discovered* decklists, not reproductions
of human lists — human decks are training signal, not output target.

## Repository layout

```
mtg-pytorch/
├── docker-compose.yml          # All services; uses traefik-public network
├── .env.example                # Copy to .env and fill in secrets
├── data/
│   └── migrations/
│       └── 001_init.sql        # Schema: cards, embeddings, synergy_edges, decks
├── services/
│   ├── api/                    # FastAPI — card search, similarity, deck generation
│   │   └── ops/
│   │       ├── commander_analysis.py  # Pure oracle-text signal extractor (no DB)
│   │       └── decks.py               # Deck generation + tribal/heuristic boosts
│   ├── ingest/                 # Pipeline: MTGJSON → pgvector embeddings
│   ├── jupyter/                # Lightweight JupyterLab image (CPU, no training deps)
│   └── ui/                     # Streamlit interface
├── models/                     # Model architecture files (shared into jupyter)
├── notebooks/                  # Jupyter notebooks (mounted into jupyter service)
└── mage/                       # XMage reference: Java rules engine (read-only)
```

## Services

| Service   | Purpose                                      | External URL (via Traefik) |
|-----------|----------------------------------------------|----------------------------|
| `db`      | pgvector/pgvector:pg16                       | internal only              |
| `api`     | FastAPI REST API                             | `$API_HOST`                |
| `ui`      | Streamlit deck builder                       | `$UI_HOST`                 |
| `jupyter` | JupyterLab for research and inference        | `$JUPYTER_HOST`            |
| `ingest`  | One-shot MTGJSON → DB pipeline               | internal only              |

## Development workflow

```bash
# 1. Bootstrap
cp .env.example .env      # edit POSTGRES_PASSWORD, hosts, ADMIN_TOKEN

# 2. Start services
docker compose up -d db api ui jupyter

# 3. Run full ingest pipeline (MTGJSON + Commander Spellbook + training artifact)
#    Stages: fetch_cards → load_cards → embed_cards → tag_abilities →
#            compute_synergy → compute_commander_value_synergy →
#            compute_tribal_typeline_synergy → import_spellbook → export_dataset
#    Takes ~30–60 min depending on hardware.
docker compose run --rm ingest

# 3a. Run a single ingest stage (useful after code changes or partial failures)
docker compose run --rm ingest python pipeline.py --stage tag_abilities
docker compose run --rm ingest python pipeline.py --stage compute_synergy
docker compose run --rm ingest python pipeline.py --stage compute_commander_value_synergy
docker compose run --rm ingest python pipeline.py --stage compute_tribal_typeline_synergy
docker compose run --rm ingest python pipeline.py --stage import_spellbook
docker compose run --rm ingest python pipeline.py --stage export_dataset
#    All valid --stage values:
#      fetch_cards, load_cards, embed_cards, tag_abilities,
#      compute_synergy, compute_commander_value_synergy,
#      compute_tribal_typeline_synergy, import_spellbook, export_dataset
#
#    tag_abilities options:
#      --rescan   Re-apply every trigger pattern to every card (not just those with
#                 0 existing rows).  Use after improving a pattern regex so that
#                 already-tagged cards pick up matches from the updated pattern.
docker compose run --rm ingest python pipeline.py --stage tag_abilities --rescan

# 4. Import decklists (required for Phase 3/4 training and proxy context in inference)
#    See "Decklist import" section below for details.
docker compose run --rm -v /path/to/exports:/data/moxfield:ro ingest python import_moxfield.py

# 5. Re-export the artifact after importing new decklists (fast — ~5 min)
docker compose run --rm ingest python pipeline.py --stage export_dataset

# 6. Restart API to clear in-process embedding cache
docker compose restart api

# 7. Rebuild pgvector index for full recall quality
docker compose exec db psql -U mtg -d mtg -c \
  "REINDEX INDEX CONCURRENTLY idx_card_embeddings_vec;"

# 8. Open UI
open https://$UI_HOST
```

### Embedding model

The embedding model must match between ingest and the trained checkpoint.
Current model: `sentence-transformers/all-mpnet-base-v2` (768-dim).
Set via `EMBEDDING_MODEL` in `.env` — must match `.env.example`.

If you ever need to switch models, delete the old rows first:
```bash
docker compose exec db psql -U mtg -d mtg -c \
  "DELETE FROM card_embeddings WHERE model = '<old-model-name>';"
docker compose exec db psql -U mtg -d mtg -c \
  "ALTER TABLE card_embeddings ALTER COLUMN embedding TYPE vector(<new-dim>);"
# then re-run ingest
```

## Two-environment setup

The system is split across two machines that must stay in sync:

| | GPU machine (training) | Docker host (serving) |
|---|---|---|
| **OS** | Windows (native, no Docker) | Linux |
| **Purpose** | Train the model | Host API, UI, DB |
| **Key files** | `services/trainer/train.py`, `scripts/*.ps1` | `docker-compose.yml`, `services/api/` |
| **docker-compose** | Not used | Primary entrypoint |
| **Setup doc** | `docs/windows-non-docker-setup.md` | This file |
| **Data source** | Downloads `mtg_dataset.pt` artifact from Docker host | Runs ingest to populate DB |
| **Output** | `.pt` checkpoint file | Serves deck generation via API |

### Sync requirements

These two must always agree or deck generation will silently fail:

- **Embedding model** — the artifact's `meta.model` field records which model was
  used.  The `input_dim` of `CardEncoder` in any checkpoint must match `meta.dim`.
  Current: `sentence-transformers/all-mpnet-base-v2` (768-dim).

- **Card universe** — if ingest is re-run on the Docker host (e.g. after a MTGJSON
  update), re-export the artifact and re-download it on the GPU machine before
  the next training run.

### Workflow for updating the model

1. Run full ingest on the Docker host (produces a fresh `mtg_dataset.pt`).
2. Import new decklists if any, then re-export: `docker compose run --rm ingest python export_dataset.py`
3. On the GPU machine, download the artifact:

```powershell
.\scripts\download_dataset.ps1
```

4. Train all phases:

```powershell
.\scripts\run.ps1 -Mode train -Phase 1 -Dataset .\ingest_cache\mtg_dataset.pt
.\scripts\run.ps1 -Mode train -Phase 2 -Dataset .\ingest_cache\mtg_dataset.pt
.\scripts\run.ps1 -Mode train -Phase 3 -Dataset .\ingest_cache\mtg_dataset.pt
.\scripts\run.ps1 -Mode train -Phase 4 -Dataset .\ingest_cache\mtg_dataset.pt
```

5. Upload the resulting checkpoint to the Docker host via the UI, or:

```bash
curl -X POST https://$API_HOST/admin/checkpoint \
  -H "x-admin-token: $ADMIN_TOKEN" \
  -F "file=@phase4_best.pt" \
  -F "name=phase4_best"
```

The API hot-swaps the model immediately (no restart needed).

Checkpoint files live in the `model_checkpoints` Docker volume, mounted at
`/app/checkpoints` in the API and `/checkpoints` in Jupyter (read-only).

## Training

Training runs on a separate GPU machine using `services/trainer/` (not part of
this compose setup).  After a training run, upload the resulting checkpoint via
the **Upload Model** tab in the UI, or directly:

```bash
curl -X POST https://$API_HOST/admin/checkpoint \
  -H "x-admin-token: $ADMIN_TOKEN" \
  -F "file=@phase4_best.pt" \
  -F "name=phase4_best"
```

The API hot-swaps the model immediately (no restart needed).

Checkpoint files live in the `model_checkpoints` Docker volume, mounted at
`/app/checkpoints` in the API and `/checkpoints` in Jupyter (read-only).

## Training progression

The model is trained in four phases, each building on the last:

1. **Text equivalence** — contrastive loss on card embeddings; same-oracle-id
   reprints are positive pairs.  Baseline: cards with identical rules text
   should be nearest neighbours.

2. **Ability-trigger synergy** — binary classifier: does card B synergise with
   card A?  Ground truth comes from `synergy_edges` (ability_trigger type)
   built by the ingest pipeline's rule-matching stage.

3. **Deck co-occurrence** — multi-label ranking: given a commander, which cards
   appear in human-built decks?  Data from EDHREC / Moxfield snapshots stored
   in the `decks` table.

4. **Generative deck construction** — transformer decoder; given commander +
   partial deck, predict next card.  Sampled freely at inference — not greedy.

## Key data sources

- **MTGJSON AtomicCards** (primary) — https://mtgjson.com/downloads/ — full
  machine-readable card data, no rate limits.  The ingest pipeline downloads
  `AtomicCards.json.gz` automatically and caches it in the `ingest_cache`
  volume.  Re-downloaded only when the MTGJSON version changes.
- **Scryfall** (fallback only) — used if MTGJSON is unavailable.  Do **not**
  hit the Scryfall API in a loop; their rate limits are strict.
- **XMage (`mage/`)** — Java reference implementation; 31 k+ files, 246 keyword
  abilities, 269 common ability patterns, full game-state engine.  Use it to
  extract structured ability information that MTGJSON keywords don't cover.
- **Moxfield** — user-curated Commander decklists exported as `.txt` files.
  Drop exports into a folder and run `import_moxfield.py` (see below).
- **cardtrak** — internal collection tracker; decklists exported via
  `ml_decklists` view and imported with `import_decklists.py`.

## Database schema (key tables)

| Table              | Purpose                                      |
|--------------------|----------------------------------------------|
| `cards`            | Oracle card data from MTGJSON/Scryfall       |
| `card_embeddings`  | Per-model vector embeddings (pgvector)       |
| `card_abilities`   | Structured ability tags (keyword/triggered)  |
| `synergy_edges`    | Pairwise synergy scores, multiple score types|
| `decks`            | Human-constructed Commander decklists        |
| `generated_decks`  | Model output decks, one row per inference    |

## Conventions

- **Python 3.12** everywhere in Python services.
- **SQLAlchemy async** with `asyncpg` in API and ingest; sync `psycopg2` in
  trainer (PyTorch DataLoader workers are synchronous).
- Embeddings dimension is **768** (all-mpnet-base-v2).  If you change the
  model, update the `vector(768)` column in the migration and add a new `model`
  row in `card_embeddings` — do not alter existing embeddings.
- The `ingest` service runs to completion (`restart: "no"`).  Restart manually
  with `docker compose run --rm ingest`.
- Traefik TLS is handled externally; services only need the labels already in
  `docker-compose.yml`.  Do not add TLS config inside containers.
- The `traefik-public` network is declared `external: true`.  Docker Compose v5
  requires the network to exist before `docker compose up` — it is created and
  managed by the Traefik stack, not this project.
- Never commit `.env`; only commit `.env.example`.

## Decklist import

Two import scripts live in `services/ingest/`:

### `import_moxfield.py` — batch Moxfield `.txt` exports

Drop Moxfield deck exports (one `.txt` per deck) into a folder, then:

```bash
docker compose run --rm \
    -v /path/to/exports:/data/moxfield:ro \
    ingest python import_moxfield.py

# Dry-run (parse only, no DB writes):
MOXFIELD_DRY_RUN=1 docker compose run --rm \
    -v /path/to/exports:/data/moxfield:ro \
    ingest python import_moxfield.py
```

- Commander identified from the `Commander` section header (reliable).
- Partner commanders: first resolving name wins as `commander_id`.
- Set/collector annotations (`(MH2) 123`) stripped automatically.
- `source = 'moxfield'`; re-importing the same file is safe (ON CONFLICT DO NOTHING).
- Default folder: `/data/moxfield`; override with `MOXFIELD_DIR` env var.

### `import_decklists.py` — cardtrak JSON export

```bash
# Export from cardtrak DB:
docker exec cardtrak_db psql -U cardtrak -d cardtrak_production \
    -t -c "SELECT json_agg(row_to_json(d)) FROM ml_decklists d \
           WHERE deck_format IN ('EDH','cedh')" > /tmp/ml_decklists.json

# Import:
docker compose run --rm \
    -v /tmp/ml_decklists.json:/data/ml_decklists.json:ro \
    ingest python import_decklists.py
```

---

## Training history

### Phase 2 — ability-trigger synergy (2026-03-14)

**Run ID:** `671fpop8` (wandb project `edh-builder`, run name `lemon-spaceship-7`)

#### Loss benchmarks

| Outcome | Final loss |
|---------|-----------|
| Barely learning | > 0.65 |
| Good | 0.55 – 0.60 |
| Excellent | 0.45 – 0.50 |
| Overfit risk | < 0.45 |

Epoch 1 baseline: **0.6610** (random baseline ≈ 0.693 — model is learning from batch 1).

#### Infrastructure lessons learned

- **synergy_edges table size** — naïve Python cartesian product (7 609 producers ×
  5 841 consumers = 44 M rows) OOM'd silently.  Fixed by rewriting `compute_synergy`
  to use chunked `INSERT…SELECT` entirely inside Postgres (`SYNERGY_CHUNK=200` cards
  per chunk, `SYNERGY_LIMIT=100_000` rows per trigger event).  First uncapped run
  produced **135 M rows / 37 GB** and filled the disk; always keep the cap in place
  and expand only after verifying the process end-to-end.

- **Sampling synergy_edges for training** — `SELECT * FROM synergy_edges` on a
  multi-million-row table OOM'd the trainer.  Fixed with
  `TABLESAMPLE SYSTEM(10) LIMIT <sample>` (default 500 k positives).  Never use
  `ORDER BY random()` on large tables — it reads the whole table first.

- **Training sampler (`--sample` flag)** — controls the maximum positive pairs
  fetched per run.  Start with the default (500 k) and reduce if memory is tight.
  Negative pairs are sampled in Python at `--neg-ratio × len(positives)` (default 3×).

- **wandb charts** — system metrics appear automatically; custom charts (loss, lr)
  require at least one `wandb.log()` call with the desired keys.  The trainer logs
  `phase`, `epoch`, `loss`, and `lr` per epoch.  If charts are missing, confirm
  `WANDB_API_KEY` is set and the run finished at least one epoch.

### Phase 3 — deck co-occurrence BPR ranking (2026-03-14)

BPR loss on (commander, positive card, random negative) triples.  Warm-started
from phase2_best.  **Final loss: 0.5432** (94 decks from cardtrak import).

- UUID[] psycopg2 bug: `card_ids` column returned as raw PG string.
  Fixed with `ARRAY(SELECT unnest(card_ids)::text)` in `load_decks()`.

### Phase 3 — rerun (2026-03-15)

**Run ID:** `neat-donkey-26` (wandb project `edh-builder`)

120 decks (94 cardtrak + 8 zombies + 9 tokens + 9 +1/+1 counters).  Also
incorporated changeling-aware tribal synergy edges from the pipeline fix.
30 epochs.  **Final loss: ~0.43** (stabilised).

Previous run: 0.5432 on 94 decks → 0.43 on 120 decks — meaningful improvement
from additional co-occurrence data.  Score compression remains (0.894–0.999
cosine range across card pool); more decks needed to further separate embeddings.

### Phase 3 — rerun 2 (2026-03-15)

278 decks (previous 120 + additional zombies, tokens, +1/+1 counters, dragons,
Tiamat, Rivaz).  Warm-started from phase3_best (neat-donkey-26).  50 epochs,
lr=1e-4.  **Best loss: 0.3922**.

0.43 → 0.3922 — continued improvement; more data is the primary driver.

### Phase 3 — rerun 3 (2026-03-16)

**Run ID:** `glorious-pond-36` (wandb project `edh-builder`)

344 decks.  Warm-started from phase3_best (rerun 2).  **Best loss: 0.2750**.

0.3922 → 0.2750 — consistent improvement with each data addition.

### Phase 4 — DeckConstructor transformer decoder (2026-03-15)

InfoNCE loss; transformer decoder cross-attends to commander embedding; 64
random negatives per position; temperature=0.1.  Warm-started from phase3_best
CardEncoder weights.  50 epochs, lr=1e-4.

**Run 1 (freeze_encoder=False, single LR):** best loss 0.0654 at epoch 12, then
collapsed to 3.8565 by epoch 50.  Root cause: encoder and decoder shared the same
lr=1e-4; after memorising 177 decks, unconstrained encoder gradients destroyed
Phase 3 representations.

**Fix:** differential learning rates — encoder at `lr * encoder_lr_scale` (default
0.1×), decoder at full lr.  Added `--encoder-lr-scale` CLI arg.

---

## Commander analysis (`GET /commanders/{oracle_id}/analyze`)

Implemented in `services/api/ops/commander_analysis.py`.  A **pure, DB-free**
heuristic layer that reads a commander's oracle text and returns structured
deckbuilding signals before (or alongside) deck generation.

### What it returns (`CommanderAnalysis`)

| Field | Description |
|-------|-------------|
| `signals` | List of `SignalResult` — each has `signal_type`, `label`, `confidence` (high/medium/low), matched `phrase`, and `boost_applied` flag |
| `gaps` | Phrases the parser couldn't interpret — shown to the user as "consider adding decklists" hints |
| `archetype_hint` | Derived from detected boost keys, e.g. `"elf tribal + elfball (mana-dork matters)"` |
| `generation_confidence` | `"high"` if ≥3 high-confidence signals with no gaps; `"medium"` otherwise |
| `boost_overrides` | Sorted list of active boost keys (e.g. `["mana_producers", "tribal"]`) — passed to generation |

### Signal extraction pipeline

1. **Card keywords** from DB (e.g. `["Flying", "Deathtouch"]`) — checked against `RULES_TERM_SIGNALS`
2. **`RULES_TERM_SIGNALS` dict** — case-insensitive substring scan of oracle text for MTG rules jargon.
   Key insight: `"mana ability"` maps to mana-dork/elfball; a plain-English parser would miss it.
   Terms with `boost=None` are recognized but also added to `gaps[]`.
3. **`_PATTERN_SIGNALS` list** — regex patterns for tribal, combat, evasion, counters, tokens, etc.
4. **Unrecognized trigger/condition detection** — novel `whenever/if/each` clauses → `gaps[]`

### Extending the dictionary

Add a new entry to `RULES_TERM_SIGNALS` in `commander_analysis.py`:

```python
"whenever you exert": _RulesTerm(
    "mechanic", "exert matters",
    "high", "exert",      # None if no boost implemented yet
),
```

That's the entire change needed.  No other files require modification.

### Canonical test case: Tyvar the Bellicose

"mana ability" is an MTG rules term meaning "activated ability that produces mana"
(mana dorks).  Without the dictionary, a parser would see generic text and miss the
entire elfball engine.  With it: `mana_producers` boost is applied, archetype hint
becomes `"elf tribal + elfball (mana-dork matters)"`, confidence `"high"`, no gaps.

Commanders with dungeon/venture mechanics correctly show gaps (recognized, no boost),
prompting the user to add decklists for that commander.

---

## XMage as a training signal

XMage's Java card implementations encode machine-readable ability structure
(triggered, activated, static, keyword) via typed Java classes.  The ingest
pipeline's `tag_abilities` stage uses regex heuristics on oracle text as a
starting point.  A richer extraction pass can parse XMage source directly
(e.g. grep `TriggeredAbilityImpl`, `ActivatedAbilityImpl`) to produce more
precise `card_abilities` rows.  This is a planned enhancement.
