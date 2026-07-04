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
│   ├── ingest/                 # Pipeline: MTGJSON → pgvector embeddings
│   │   ├── pipeline.py         # Stage orchestrator — accepts --stage flag
│   │   ├── stages/             # Focused stage modules (pipeline.py delegates here)
│   │   │   ├── db.py           #   Shared engine, Session, SYNERGY_CHUNK constants
│   │   │   ├── download.py     #   Fetch MTGJSON/Scryfall + load cards + import combos
│   │   │   ├── facts.py        #   compute_card_facts — Layer-1 facts → card_facts table
│   │   │   ├── tag.py          #   embed_cards
│   │   │   ├── mechanics.py    #   tag_mechanics — canonical role tagger (coarse + fine + oracle-pattern)
│   │   │   ├── dataset.py      #   compute_textmatch_synergy + compute_xmage_synergy + compute_xmage_effect_synergy
│   │   │   ├── commander.py    #   compute_commander_value_synergy
│   │   │   ├── decompose.py    #   decompose_commanders — ORACLE_PATTERNS + _detect()
│   │   │   └── export.py       #   Thin wrappers for all export sub-stages
│   │   └── scripts/            # User-facing scripts (export, import, eval)
│   │       ├── export_dataset.py           #   Build mtg_dataset.pt (Phases 1–2)
│   │       ├── export_dataset_commanders.py #   Build mtg_commanders.pt (Phases 3–4)
│   │       ├── export_db_helpers.py        #   Shared DB loading utilities
│   │       ├── eval_decomposition.py       #   Spot-check decompose output (DB required)
│   │       ├── import_moxfield.py          #   Batch Moxfield .txt deck imports
│   │       ├── import_decklists.py         #   cardtrak JSON export imports
│   │       └── import_spellbook.py         #   Commander Spellbook combo imports
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
| `ui`      | Streamlit deck builder + generated deck history | `$UI_HOST`              |
| `jupyter` | JupyterLab for research and inference        | `$JUPYTER_HOST`            |
| `ingest`  | One-shot MTGJSON → DB pipeline               | internal only              |

```mermaid
graph TB
    subgraph docker["Docker Host (Linux)"]
        DB[(pgvector\nDB)]
        ING[Ingest\npipeline.py]
        API[FastAPI\napi]
        UI[Streamlit\nui]
        JL[JupyterLab\njupyter]
    end
    subgraph gpu["GPU Machine (Windows)"]
        TR["train.py\n+ scripts/*.ps1"]
    end
    ING -->|"mtg_dataset.pt\nmtg_commanders.pt\n(download_dataset.ps1)"| TR
    TR -->|"phase*.pt checkpoints\n(curl upload)"| API
    DB <--> ING
    DB <--> API
    API <--> UI
    API <--> JL
```

## Development workflow

```bash
# 1. Bootstrap
cp .env.example .env      # edit POSTGRES_PASSWORD, hosts, ADMIN_TOKEN

# 2. Start services
docker compose up -d db api ui jupyter

# 3. Download card data + combos (MTGJSON → cards table + Commander Spellbook)
#    Re-run when new sets release or combo data changes.  Fast — no ML work.
docker compose run --rm ingest python pipeline.py --stage download

# 4. Process: embed, tag abilities, compute synergy edges, export artifact
#    Requires download to have been run first.  Takes ~30–60 min.
docker compose run --rm ingest python pipeline.py --stage process

# 3+4 combined (full pipeline, same as default):
docker compose run --rm ingest

# Individual sub-stages (useful after code changes or partial failures):
docker compose run --rm ingest python pipeline.py --stage compute_card_facts   # Layer-1 card facts (pips, land classes) → card_facts table
docker compose run --rm ingest python pipeline.py --stage embed_cards
docker compose run --rm ingest python pipeline.py --stage tag_mechanics
docker compose run --rm ingest python pipeline.py --stage tag_mechanics --rescan   # delete + re-insert all oracle_text/card_characteristic role rows
docker compose run --rm ingest python pipeline.py --stage tag_abilities_xmage          # supplement with XMage source parsing (requires mage/ mount)
docker compose run --rm ingest python pipeline.py --stage compute_textmatch_synergy
docker compose run --rm ingest python pipeline.py --stage compute_xmage_synergy
docker compose run --rm ingest python pipeline.py --stage export_dataset

# Commander artifact pipeline (required before export_dataset_commanders):
# These stages are NOT part of process — run them explicitly after process
# before building mtg_commanders.pt.

# Step 0: write decompose signals to card_abilities (source='decompose')
#   prerequisite for export_dataset_commanders; also fixes the UI decompose panel.
docker compose run --rm ingest python pipeline.py --stage decompose_commanders

# Step 1: commander-value synergy edges
# (tribal edges are built by compute_textmatch_synergy via commander_mechanics.py)
docker compose run --rm ingest python pipeline.py --stage compute_commander_value_synergy

# Step 2: export artifact (reads card_abilities instead of calling _detect directly)
docker compose run --rm ingest python pipeline.py --stage export_dataset_commanders

# Spot-check the decomposition output with eval_decomposition:
docker compose run --rm ingest python -m scripts.eval_decomposition "Anje Falkenrath"         # named lookup (partial match)
docker compose run --rm ingest python -m scripts.eval_decomposition --no-signals              # list commanders with zero signals (gap analysis)
docker compose run --rm ingest python -m scripts.eval_decomposition --key goad                # evaluate a specific pattern key
docker compose run --rm ingest python -m scripts.eval_decomposition "Anje" --limit 0          # remove per-key card cap (default 10)

# 5. Restart API to clear in-process embedding cache
docker compose restart api

# 8. Rebuild pgvector index for full recall quality
docker compose exec db psql -U mtg -d mtg -c \
  "REINDEX INDEX CONCURRENTLY idx_card_embeddings_vec;"

# 9. Open UI
open https://$UI_HOST
```

### Ingest pipeline stages

```mermaid
flowchart TD
    DL["download\n(MTGJSON + combos)"]
    EC["embed_cards\n(sentence-transformers 768-dim)"]
    TM["tag_mechanics\n(coarse + fine + oracle-pattern)"]
    XM["tag_abilities_xmage\n(XMage Java source)"]
    TTS["compute_textmatch_synergy\n→ ability_trigger edges"]
    XS["compute_xmage_synergy\n→ xmage_ability_trigger edges"]
    XES["compute_xmage_effect_synergy\n→ effect_peer edges"]
    ED["export_dataset\n→ mtg_dataset.pt"]
    DC["decompose_commanders\n→ card_abilities source='decompose'"]
    CVS["compute_commander_value_synergy\n→ decomposed_candidates edges"]
    EDC["export_dataset_commanders\n→ mtg_commanders.pt"]

    DL --> EC
    EC --> TM
    TM --> XM
    TM --> TTS
    XM --> XS
    XM --> XES
    TTS --> ED
    XS --> ED
    XES --> ED

    TM --> DC
    DC --> CVS
    TTS -.->|"tribal edges"| EDC
    CVS --> EDC
```

`download` + `embed_cards` + `tag_mechanics` + synergy stages + `export_dataset` are all run by `--stage process`.  The commander artifact stages (`decompose_commanders`, `compute_commander_value_synergy`, `export_dataset_commanders`) must be run explicitly after `process`.

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
2. Import new decklists if any, then re-export: `docker compose run --rm ingest python pipeline.py --stage export_dataset`
3. On the GPU machine, download the artifact:

```powershell
.\scripts\download_dataset.ps1
```

4. Train all phases:

```powershell
.\scripts\run.ps1 -Mode train -Phase 1 -Dataset .\ingest_cache\mtg_dataset.pt
.\scripts\run.ps1 -Mode train -Phase 2 -Dataset .\ingest_cache\mtg_dataset.pt   # bilinear (default)
.\scripts\run.ps1 -Mode train -Phase 3 -Dataset .\ingest_cache\mtg_commanders.pt
.\scripts\run.ps1 -Mode train -Phase 4 -Dataset .\ingest_cache\mtg_commanders.pt
```

Phase 2 trains `BilinearSynergyHead` (saves `phase2_bilinear_best.pt`) with the
encoder frozen at `phase1_best.pt`.  `phase2_best.pt` is **not** written by the
bilinear path — Phase 3 still loads the encoder from `phase1_best.pt`.  Upload
both checkpoints to the API after training:

5. Upload checkpoints to the Docker host via the UI, or:

```bash
# Phase 1 encoder (required by Phase 3 and as fallback encoder)
curl -X POST https://$API_HOST/admin/checkpoint \
  -H "x-admin-token: $ADMIN_TOKEN" \
  -F "file=@phase1_best.pt" \
  -F "name=phase1_best"

# Phase 2 bilinear head (enables relation-aware inference scoring)
curl -X POST https://$API_HOST/admin/checkpoint \
  -H "x-admin-token: $ADMIN_TOKEN" \
  -F "file=@phase2_bilinear_best.pt" \
  -F "name=phase2_bilinear_best"

# Phase 3/4 CommanderScorer (primary deck-building scorer)
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

2. **Relational bilinear scoring** — learns one weight matrix W_r per relation
   type (`effect_peer`, `ability_trigger`, `combo`, `decomposed_candidates`).
   The Phase 1 encoder is **frozen**; only the W_r matrices move.  Score:
   `score(A, B, r) = A^T W_r B`.  Trained with asymmetric InfoNCE per relation.
   Replaces the previous NT-Xent formulation which corrupted Phase 1 geometry
   by conflating complementary relations (producer→consumer, combo) with
   similarity relations (functional peers).

3. **Commander-card ranking** — BPR loss on the commanders artifact: given a
   commander, rank its synergy-positive cards above random legal cards.
   Trains `CommanderScorer` (a joint MLP over `[z_cmd; z_card]`) on top of
   the frozen Phase 1 encoder.  Adds per-commander non-linear discrimination
   that the Phase 2 bilinear head cannot represent (W_r is a single global
   matrix; the MLP sees each commander–card pair jointly).  **Empirically
   validate** that Phase 3 re-ranks the top-20 candidates differently from the
   bilinear head alone before treating it as load-bearing — if the candidate
   pre-filter and bilinear signal are already tight, Phase 3 may provide
   diminishing returns.

4. **Generative deck construction** — transformer decoder; given commander +
   partial deck, predict next card.  Sampled freely at inference — not greedy.
   The only phase that models deck-level coherence: no scoring function over a
   fixed (commander, card) pair can account for what is already in the deck.

```mermaid
flowchart LR
    DS[("mtg_dataset.pt")]
    DC[("mtg_commanders.pt")]

    P1["Phase 1\nCardEncoder\nNT-Xent\ncontrastive loss"]
    P2["Phase 2\nBilinearSynergyHead\nasymmetric InfoNCE\nper relation\n(encoder frozen)"]
    P3["Phase 3\nCommanderScorer MLP\nBPR ranking\n(encoder frozen)"]
    P4["Phase 4\nTransformer Decoder\nautoregressive\ndeck construction"]

    DS --> P1
    DS --> P2
    DC --> P3
    DC --> P4

    P1 -->|"phase1_best.pt\nencoder weights"| P2
    P1 -->|"phase1_best.pt\nencoder weights"| P3
    P2 -->|"phase2_bilinear_best.pt"| INF
    P3 -->|"phase3_best.pt"| P4
    P4 -->|"phase4_best.pt"| INF[("API\nInference\n30% bilinear\n70% scorer")]
```

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
| `card_facts`       | Layer-1 composition facts: pip counts, land classification (see `shared/composition/`) |
| `card_embeddings`  | Per-model vector embeddings (pgvector)       |
| `card_abilities`   | Structured ability tags (keyword/triggered)  |
| `synergy_edges`    | Pairwise synergy scores, multiple score types|
| `decks`            | Commander decklists (synthesized training data) |
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

## Training notes

See [`docs/training-notes.md`](docs/training-notes.md) for: Phase 2 loss benchmarks, Phase 4 encoder stability settings, land embedding invalidation procedure, training artifact reference, commander artifact details, and the full Phase 2 bilinear relation type guide (relation semantics, training flow, updating pairs, adding new relations).

---

## UI tabs

The Streamlit UI (`services/ui/app.py`) has two tabs:

| Tab | Purpose |
|-----|---------|
| **Deck Builder** | Search for a commander, score candidates with CommanderScorer. |
| **Generated Decks** | Browse and inspect all previously generated decks.  Auto-selects the most recently generated deck when navigating from the builder. |

The `app.py` is **baked into the Docker image** — changes require a rebuild:

```bash
docker compose build ui && docker compose up -d ui
```

### Generated deck persistence

Completed decks are saved as timestamped JSON files under `DECK_SAVE_DIR`
(default `/app/generated_decks` inside the API container, backed by the
`generated_decks` Docker volume).  Two API endpoints expose this history:

| Endpoint | Description |
|----------|-------------|
| `GET /decks/generated` | List saved decks (newest first): filename, commander, checkpoint, card count |
| `GET /decks/generated/{filename}` | Fetch full deck JSON by filename |

The job result for `GET /decks/jobs/{job_id}` includes a `deck_filename` field
once the job is complete, so the UI can deep-link directly to the saved file.

---

## Evaluation scripts (GPU machine, no DB required)

All eval scripts load from the training artifact — no database connection needed.

### `eval_neighbors.ps1` — nearest-neighbour spot-check

Verifies Phase 1 checkpoint quality by projecting all card embeddings through
the trained `CardEncoder` and printing the top-N nearest neighbours for a
given card.  Use this to confirm that functionally equivalent cards cluster
together after training.

```powershell
.\scripts\eval_neighbors.ps1 "Swords to Plowshares"
.\scripts\eval_neighbors.ps1 "Llanowar Elves" -Top 30
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `-Card` | (required) | Card name — partial/case-insensitive match |
| `-Top` | `20` | Number of neighbours to display |
| `-Checkpoint` | `phase1_best` | Override checkpoint name |
| `-Dataset` | `ingest_cache\mtg_dataset.pt` | Override artifact path |

**Expected results (Phase 1 success criteria):**
- Swords to Plowshares → Path to Exile, Generous Gift (removal cluster)
- Llanowar Elves → Birds of Paradise, Elvish Mystic, Fyndhorn Elves (ramp cluster)


---

## XMage as a training signal

See [`docs/xmage-signal.md`](docs/xmage-signal.md) for: how `xmage_parse.py` works, `ABILITY_CLASS_TO_EVENT` mapping, body-scan refinements, and the `SpellCastControllerTriggeredAbility` per-spell-type bucket breakdown.

---

## Commander decomposition (`stages/decompose.py`)

See [`docs/commander-decomposition.md`](docs/commander-decomposition.md) for the full reference: signal sources, the complete pattern library, spot-check commands, and instructions for adding new patterns.
