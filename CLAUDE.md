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
│   │   └── stages/             # Focused stage modules (pipeline.py delegates here)
│   │       ├── db.py           #   Shared engine, Session, SYNERGY_CHUNK constants
│   │       ├── download.py     #   Fetch MTGJSON/Scryfall + load cards + import combos
│   │       ├── tag.py          #   embed_cards
│   │       ├── mechanics.py    #   tag_mechanics — canonical role tagger (coarse + fine + oracle-pattern)
│   │       ├── dataset.py      #   compute_textmatch_synergy + compute_xmage_synergy + compute_xmage_effect_synergy
│   │       ├── commander.py    #   compute_commander_value_synergy
│   │       └── export.py       #   Thin wrappers for all export sub-stages
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

# Spot-check the decomposition output with eval_commander.py:
docker compose run --rm ingest python scripts/eval_commander.py "Anje Falkenrath"   # named lookup (partial match)
docker compose run --rm ingest python scripts/eval_commander.py --stats             # coverage summary + pattern frequency histogram
docker compose run --rm ingest python scripts/eval_commander.py --no-signals        # list commanders with zero signals (gap analysis)
docker compose run --rm ingest python scripts/eval_commander.py --pattern goad      # list all commanders that matched a specific pattern_key
docker compose run --rm ingest python scripts/eval_commander.py --pattern goad --limit 0  # remove cap (default 50)

# 5. Import decklists (required for Phase 3/4 training and proxy context in inference)
#    See "Decklist import" section below for details.
docker compose run --rm -v /path/to/exports:/data/moxfield:ro ingest python import_moxfield.py

# 6. Re-export the artifact after importing new decklists (fast — ~5 min)
docker compose run --rm ingest python pipeline.py --stage export_dataset

# 7. Restart API to clear in-process embedding cache
docker compose restart api

# 8. Rebuild pgvector index for full recall quality
docker compose exec db psql -U mtg -d mtg -c \
  "REINDEX INDEX CONCURRENTLY idx_card_embeddings_vec;"

# 9. Open UI
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
.\scripts\run.ps1 -Mode train -Phase 3 -Dataset .\ingest_cache\mtg_commanders.pt
.\scripts\run.ps1 -Mode train -Phase 4 -Dataset .\ingest_cache\mtg_commanders.pt
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

## Training notes

### Phase 2 — loss benchmarks

Phase 2 uses **NT-Xent (InfoNCE)** loss, not BCE.  The loss scale depends on
batch size: the random (no-learning) ceiling is `ln(2 × batch_size)`.

| batch_size | Random ceiling | Barely learning | Good | Excellent | Overfit risk |
|-----------|---------------|-----------------|------|-----------|--------------|
| 256 | ln(512) ≈ 6.24 | > 5.8 | 4.5 – 5.5 | 3.0 – 4.5 | < 3.0 |
| 512 | ln(1024) ≈ 6.93 | > 6.5 | 5.0 – 6.2 | 3.5 – 5.0 | < 3.5 |

The default run.ps1 batch size for Phase 2 is 512.  A final loss of ~6.1 at the
midpoint of training (epoch 30/60, temperature still at 0.15) is normal — most
learning happens in the second half as temperature anneals toward `--temp-end`
(default 0.07).  Evaluate geometry with `eval_neighbors.ps1`, not raw loss alone.

> **Note:** benchmarks prior to this entry were calibrated for the old BCE
> formulation (loss in [0, 1]).  They are no longer applicable.

The trainer uses `TABLESAMPLE SYSTEM(10) LIMIT 500_000` to sample positives from
`synergy_edges` — never `ORDER BY random()` on the full table.  The `--sample`
flag controls the positive count; `--neg-ratio` (default 3×) controls negatives.
`compute_textmatch_synergy` runs inside Postgres with `SYNERGY_CHUNK=200` and
`SYNERGY_LIMIT=100_000` rows per trigger event — keep the cap in place.

### Phase 4 — encoder stability

The encoder is unfrozen by default but runs at `lr * encoder_lr_scale` (default
0.1×) to protect Phase 3 representations.  Pass `-FreezeEncoder true` in
`run.ps1` to freeze entirely.  `patience=10` halts training if loss does not
improve for 10 consecutive epochs.  Score compression (cosine similarity → 1.0
across all pairs) indicates the encoder has been over-updated; reduce
`encoder_lr_scale` or freeze.

### Land embeddings

`services/ingest/land_tags.py` prepends structured tags to every Land card's
oracle text before embedding (fetch, dual, shock, check, etc. cycles; penalty
tags for tapped/sacrifice).  If `land_tags.py` changes, delete and re-embed:

```bash
docker compose exec db psql -U mtg -d mtg -c "
  DELETE FROM card_embeddings
  WHERE card_id IN (
    SELECT e.card_id FROM card_embeddings e
    JOIN cards c ON c.id = e.card_id
    WHERE c.type_line ILIKE '%Land%'
  );"
docker compose run --rm ingest python pipeline.py --stage embed_cards
docker compose run --rm ingest python pipeline.py --stage export_dataset
```

Changing land embeddings invalidates all checkpoints — retrain from Phase 1.

### Training artifacts

| Phases | Artifact | Checkpoint prefix |
|--------|----------|--------------------|
| 1–2 | `mtg_dataset.pt` | `phase*` |
| 3–4 | `mtg_commanders.pt` | `phase*` |

```powershell
.\scripts\download_dataset.ps1      # downloads mtg_dataset.pt (Phases 1-2)
.\scripts\download_commanders.ps1   # downloads mtg_commanders.pt (Phases 3-4)

.\scripts\run.ps1 -Train 1
.\scripts\run.ps1 -Train 2
.\scripts\run.ps1 -Train 3
.\scripts\run.ps1 -Train 4
```

### Commander artifact (`mtg_commanders.pt`)

The commander artifact enables Phase 3 BPR training **without human decklists**,
avoiding the representation-collapse failure mode where all commanders converge
toward an indistinct high-similarity cluster because they all need the same
generic roles (draw, ramp, removal).

**How it works:** `export_dataset_commanders.py` reads directly from `synergy_edges`
— no JSON intermediate required.  For each legal commander two edge types contribute
positives: `ability_trigger` edges (producers of the commander's trigger → commander)
and `commander_value` edges (commander → payoff cards).  Color-identity legality is
re-applied strictly (⊆) in Python.  The result is a per-commander positive set that
is genuinely distinct from other commanders', giving BPR a meaningful gradient.

```bash
# Requires compute_textmatch_synergy and compute_commander_value_synergy to have been run.
docker compose run --rm ingest python pipeline.py --stage export_dataset_commanders

# Or call directly:
docker compose run --rm ingest python export_dataset_commanders.py
```

| Env var | Default | Purpose |
|---------|---------|---------|
| `COMMANDERS_OUTPUT` | `/data/mtg_commanders.pt` | Output artifact path |
| `COMMANDERS_MIN_POS` | `10` | Skip commanders with fewer producer cards |
| `COMMANDERS_MAX_POS` | `300` | Cap per-commander positives (shuffle + truncate) |

The artifact schema is identical to `mtg_dataset.pt` for the `decks` key
(`commander_idx`, `card_idxs`, `color_identity`, `legal_neg_indices`, `archetype`)
so the existing `DeckDataset` and `train_deck_phase` in `train.py` work unchanged.
The `archetype` field contains the top-5 most frequent `trigger_event` values from
the commander's edges (e.g. `"creature_etb, death_trigger, tribal_zombie_typeline"`).

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

XMage's Java card implementations encode machine-readable ability structure
(triggered, activated, static, keyword) via typed Java classes.
`tag_abilities_xmage` supplements the canonical `tag_mechanics` stage with
a pass over the XMage Java source tree (`mage/`) and writes `card_abilities`
rows tagged `source='xmage'`.

### How `xmage_parse.py` works

`parse_java_file(path)` returns `(ability_classes, effect_classes, trigger_event_overrides)`:

- **ability_classes** — XMage ability class names extracted from `import mage.abilities.common.*` and `import mage.abilities.keyword.*` statements.
- **effect_classes** — effect class names from `import mage.abilities.effects.common.*`.
- **trigger_event_overrides** — `dict[str, str]` mapping ability class → refined `trigger_event` for any class where a body scan can narrow the default.  Empty dict when nothing is refined.

`ABILITY_CLASS_TO_EVENT` maps class names to generic `trigger_event` strings (e.g. `SpellCastControllerTriggeredAbility` → `"spell_cast"`).  The caller resolves `trigger_event_overrides.get(ac) or ABILITY_CLASS_TO_EVENT.get(ac)` so body-scan results take priority.

Adding a new body-scan refinement is two steps: add a regex + lookup in the body-scan block of `parse_java_file`, and populate the result into `trigger_event_overrides`.  No signature change needed.

The upsert uses `ON CONFLICT DO UPDATE SET trigger_event = EXCLUDED.trigger_event` so re-running `tag_abilities_xmage` refreshes refined values without a manual delete.

### SpellCastControllerTriggeredAbility filter refinement

XMage uses one class (`SpellCastControllerTriggeredAbility`) for all "whenever you cast X" triggers but passes a `StaticFilters.FILTER_SPELL_*` constant as the second constructor argument to restrict the spell type.  Without refinement all 96 cards using this ability land in a single `spell_cast` bucket — putting Sythis (enchantment cast) in the same positive-pair cluster as Guttersnipe (instant/sorcery) and Beast Whisperer (creature cast), which corrupts Phase 2 NT-Xent training.

**How it works end-to-end:**

1. `parse_java_file` scans the Java body for `StaticFilters.(FILTER_SPELL_\w+)` when `SpellCastControllerTriggeredAbility` is imported.  `SPELLCAST_FILTER_MAP` translates the constant to a refined `trigger_event` (e.g. `FILTER_SPELL_AN_ENCHANTMENT` → `"enchantment_cast"`), which is stored in `trigger_event_overrides`.

2. `tag_abilities_xmage` writes the refined `trigger_event` into `card_abilities`.

3. `compute_xmage_synergy` (in `pipeline.py`) detects `SpellCastControllerTriggeredAbility` and queries the distinct `trigger_event` values present in `card_abilities` for that class.  Each sub-bucket is processed independently using `SPELLCAST_TRIGGER_PRODUCER_MAP` in `synergy/xmage.py` to select the correct producer cards (e.g. only enchantments for `enchantment_cast`).

| `trigger_event` | Producer cards selected |
|---|---|
| `enchantment_cast` | cards with `enchantment` in type_line |
| `artifact_cast` | cards with `artifact` in type_line |
| `creature_cast` | cards with `creature` in type_line |
| `instant_sorcery_cast` | instants and sorceries |
| `noncreature_cast` | non-creature, non-land cards |
| `historic_cast` | artifacts, legendaries, sagas |
| `spirit_arcane_cast` | spirits and arcane spells |
| `spell_cast` | any non-land card (generic fallback) |

Cards with no recognised `StaticFilters` argument keep `trigger_event='spell_cast'` and use the generic `_ANY_SPELL` producer SQL.

---

## Commander decomposition (`stages/decompose.py`)

Decomposes all ~3,000 legal commanders into structured synergy signals.  **Required pipeline step** — writes `card_abilities` rows with `source='decompose'` that `export_dataset_commanders` reads when building per-commander positive sets.  Also drives the UI decompose panel (`GET /commanders/{oracle_id}/decompose`).

Run after `process` (requires populated `cards` and `card_abilities` tables), and before `compute_commander_value_synergy`.

```bash
docker compose run --rm ingest python pipeline.py --stage decompose_commanders
```

Detection logic lives in `stages/decompose.py` (`ORACLE_PATTERNS`, `_detect()`).  `export_dataset_commanders.py` reads the resulting `card_abilities` rows — it does **not** call `_detect()` directly.  This ensures the UI and the training artifact are always consistent.

### Signal sources

Each `card_abilities` row written by `decompose_commanders` comes from:

| Source | How it works |
|--------|-------------|
| `oracle_text` | ~32 regex patterns in `ORACLE_PATTERNS` against the commander's rules text.  Each match produces a `pattern_key` (`trigger_event`), human-readable label (`ability_name`), and matched phrase (`raw_text`). |

### Pattern library (oracle text)

| Pattern key | Description | Notable commanders |
|---|---|---|
| `etb_trigger` | ETB trigger (generic + proper-name subjects) | Panharmonicon payoffs |
| `attack_trigger` | Attack trigger | Isshin, Raiyuu, Gahiji |
| `cast_trigger_creature` | Creature cast trigger | Beast Whisperer analogues |
| `cast_trigger_instant_sorcery` | Instant/sorcery cast trigger | Guttersnipe analogues |
| `cast_trigger_enchantment` | Enchantment cast trigger | Sythis, Eidolon |
| `cast_trigger_artifact` | Artifact cast trigger | Breya, Daretti |
| `cast_trigger_historic` | Historic spell cast trigger | Jhoira, Teshar, Sarah Jane Smith |
| `cast_trigger_colored` | Color-based cast trigger | Chandra, K'rrik, Aragorn |
| `death_trigger` | Creature death trigger | Syr Konrad, Teysa |
| `graveyard_from_play` | Permanent to graveyard | Meren |
| `graveyard_payoff` | Cast/return from graveyard | Karador, Muldrotha |
| `unearth_encore` | Unearth / encore / temporary reanimation (haste, exile/sacrifice at end step) | Sedris, Burakos, Feldon |
| `combat_damage_to_player` | Combat damage to player | Voltron payoffs |
| `sacrifice_payoff` | Sacrifice outlet/payoff | Prossh, Korvold |
| `discard_outlet` | Discard outlet | Anje, Waste Not |
| `madness_payoff` | Madness | Anje Falkenrath |
| `landfall` | Landfall | Omnath variants |
| `counter_placement` | +1/+1 counter placement | Atraxa, Ezuri |
| `counter_doubler` | Counter doubling | Vorinclex |
| `proliferate_matters` | Proliferate | Atraxa |
| `lifegain_trigger` | Life gain trigger | Oloro, Dina |
| `draw_trigger` | Draw trigger | Niv-Mizzet |
| `token_trigger` | Token creation trigger | Brudiclad |
| `trigger_doubling` | Trigger doubling | Isshin, Wulfgar |
| `keyword_lord` | Keyword grant to creatures | Odric, Akroma |
| `cycling_trigger` | Cycling trigger | Gavi, Ominous Seas |
| `second_spell` | Second spell matters | Veyran |
| `punisher` | Damage/drain each opponent | Mogis, Nekusar |
| `weenie_matters` | Low-power creature payoff | Edric |
| `extra_combat` | Extra combat phase | Aurelia, Moraug, Raiyuu |
| `equipment_matters` | Equipment ETB/attack/static | Kemba, Sram, Wyleth, Akiri |
| `artifact_count` | Artifact count matters (scales with # of artifacts) | Akiri Line-Slinger, Saheeli, Muzzio, Alibou |
| `artifact_creatures` | Artifact creatures matter (buffs/triggers off artifact creatures) | Alibou, Brudiclad, Padeem, Teshar, Sydri |
| `opponent_restriction` | Opponents can't (stax) | Narset, Dragonlord Dromoka |
| `activated_restriction` | Activated abilities locked (stax) | Linvala, Karn |
| `tax_effect` | Opponents' spells cost more | Grand Arbiter |
| `enters_tapped_opponent` | Opponents' permanents enter tapped | Thalia Heretic Cathar |
| `monarch` | Monarch mechanic | Queen Marchesa, Aragorn |
| `initiative` | Initiative mechanic | Rilsa Rael, Safana |
| `goad` | Goad | Karazikar, Marisi, Kitt Kanto |
| `forced_attack` | Attacks each combat if able | Thantis, Zurgo, Toski |
| `poison_infect` | Infect / toxic / poison counter | Skithiryx, Fynn, Ixhel |
| `cascade` | Cascade / discover | Yidris, Abaddon, Maelstrom Wanderer, Averna |
| `group_hug` | Draw/resource grants to all players | Kami, Kwain, Kynaios and Tiro |

### Spot-check with `eval_commander.py`

```bash
# Named lookup (partial, case-insensitive):
docker compose run --rm ingest python scripts/eval_commander.py "Anje"
docker compose run --rm ingest python scripts/eval_commander.py "Syr Konrad, the Grim"

# Coverage summary + pattern frequency histogram:
docker compose run --rm ingest python scripts/eval_commander.py --stats

# Gap analysis — commanders with zero signals:
docker compose run --rm ingest python scripts/eval_commander.py --no-signals

# All commanders matching a specific pattern_key:
docker compose run --rm ingest python scripts/eval_commander.py --pattern goad
docker compose run --rm ingest python scripts/eval_commander.py --pattern goad --limit 0  # remove 50-result cap
```

### Adding a new pattern

Add one entry to `ORACLE_PATTERNS` in `stages/decompose.py`:

```python
("pattern_key",
 "Human-readable label",
 re.compile(r"your regex here", re.I)),
```

Then re-run `decompose_commanders` and spot-check with `eval_commander.py --pattern <key>`.  No other files require modification.
