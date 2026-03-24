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
docker compose run --rm ingest python pipeline.py --stage tag_abilities
docker compose run --rm ingest python pipeline.py --stage tag_abilities --rescan   # re-apply all patterns to all cards
docker compose run --rm ingest python pipeline.py --stage tag_abilities_xmage      # supplement with XMage source parsing (requires mage/ mount)
docker compose run --rm ingest python pipeline.py --stage compute_synergy
docker compose run --rm ingest python pipeline.py --stage compute_commander_value_synergy
docker compose run --rm ingest python pipeline.py --stage compute_tribal_typeline_synergy
docker compose run --rm ingest python pipeline.py --stage export_dataset

# Research: decompose all ~3000 commanders into synergy signals (read-only, outputs JSON)
docker compose run --rm ingest python scripts/decompose_commanders.py
# Spot-check the decomposition output:
docker compose run --rm ingest python scripts/eval_commander.py "Anje Falkenrath"
docker compose run --rm ingest python scripts/eval_commander.py --stats
docker compose run --rm ingest python scripts/eval_commander.py --no-signals

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

| Outcome | Final loss |
|---------|-----------|
| Barely learning | > 0.65 |
| Good | 0.55 – 0.60 |
| Excellent | 0.45 – 0.50 |
| Overfit risk | < 0.45 |

The trainer uses `TABLESAMPLE SYSTEM(10) LIMIT 500_000` to sample positives from
`synergy_edges` — never `ORDER BY random()` on the full table.  The `--sample`
flag controls the positive count; `--neg-ratio` (default 3×) controls negatives.
`compute_synergy` runs inside Postgres with `SYNERGY_CHUNK=200` and
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

### Training path

Two parallel training paths run side by side (see #71):

| Path | Artifact | Checkpoint prefix |
|------|----------|-------------------|
| Co-occurrence | `mtg_dataset.pt` | `phase*` |
| Compositional | `mtg_dataset_compositional.pt` | `comp_phase*` |

The compositional artifact is produced by a separate export stage:

```bash
docker compose run --rm ingest python pipeline.py --stage export_dataset_compositional
```

On the GPU machine, download it alongside the standard artifact and train with
`-TrainingPath compositional`:

```powershell
.\scripts\download_dataset.ps1          # downloads mtg_dataset.pt
# (download mtg_dataset_compositional.pt manually or extend the script)

.\scripts\run.ps1 -TrainingPath compositional -Train 1
.\scripts\run.ps1 -TrainingPath compositional -Train 2
.\scripts\run.ps1 -TrainingPath compositional -Train 3
.\scripts\run.ps1 -TrainingPath compositional -Train 4
```

Phase 1 of the compositional path uses **functional equivalence pairs** from
`card_abilities` instead of noise-augmented single-card views.  Two cards are
paired when they share the same ability role (e.g. `ramp`, `removal`), color
identity bucket, and CMC bracket — so Llanowar Elves and Elvish Mystic are
positive pairs, not just reprints of the same oracle text.

---

## UI tabs

The Streamlit UI (`services/ui/app.py`) has two tabs:

| Tab | Purpose |
|-----|---------|
| **Deck Builder** | Search for a commander, run analysis, generate a deck.  On completion shows a progress-complete notice and directs the user to Generated Decks. |
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
.\scripts\eval_neighbors.ps1 "Swords to Plowshares" -TrainingPath cooccurrence
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `-Card` | (required) | Card name — partial/case-insensitive match |
| `-TrainingPath` | `compositional` | Selects checkpoint prefix and artifact |
| `-Top` | `20` | Number of neighbours to display |
| `-Checkpoint` | `<prefix>1_best` | Override checkpoint name |
| `-Dataset` | `ingest_cache\mtg_dataset[_compositional].pt` | Override artifact path |

**Expected results (Phase 1 compositional success criteria):**
- Swords to Plowshares → Path to Exile, Generous Gift (removal cluster)
- Llanowar Elves → Birds of Paradise, Elvish Mystic, Fyndhorn Elves (ramp cluster)


---

## XMage as a training signal

XMage's Java card implementations encode machine-readable ability structure
(triggered, activated, static, keyword) via typed Java classes.  The ingest
pipeline's `tag_abilities` stage uses regex heuristics on oracle text as a
starting point.  `tag_abilities_xmage` supplements this with a pass over the
XMage Java source tree (`mage/`) and writes `card_abilities` rows tagged
`source='xmage'`.

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

3. `compute_synergy_xmage` (in `pipeline.py`) detects `SpellCastControllerTriggeredAbility` and queries the distinct `trigger_event` values present in `card_abilities` for that class.  Each sub-bucket is processed independently using `SPELLCAST_TRIGGER_PRODUCER_MAP` in `synergy/xmage.py` to select the correct producer cards (e.g. only enchantments for `enchantment_cast`).

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
