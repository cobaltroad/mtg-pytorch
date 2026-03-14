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
│   ├── ingest/                 # Pipeline: Scryfall → pgvector embeddings
│   ├── trainer/                # PyTorch training (also runs Jupyter Lab)
│   └── ui/                     # Streamlit interface
├── models/                     # Model architecture files (shared into trainer)
├── notebooks/                  # Jupyter notebooks (mounted into jupyter service)
└── mage/                       # XMage reference: Java rules engine (read-only)
```

## Services

| Service   | Purpose                                      | External URL (via Traefik) |
|-----------|----------------------------------------------|----------------------------|
| `db`      | pgvector/pgvector:pg16                       | internal only              |
| `api`     | FastAPI REST API                             | `$API_HOST`                |
| `ui`      | Streamlit card search + deck builder         | `$UI_HOST`                 |
| `jupyter` | JupyterLab for research (uses trainer image) | `$JUPYTER_HOST`            |
| `ingest`  | One-shot Scryfall → DB pipeline              | internal only              |
| `trainer` | PyTorch training (one-shot / GPU)            | internal only              |

## Development workflow

```bash
# 1. Bootstrap
cp .env.example .env      # edit POSTGRES_PASSWORD, hosts, WANDB_API_KEY

# 2. Start DB + API + UI
docker compose up -d db api ui jupyter

# 3. Run ingest (downloads ~90 MB Scryfall bulk JSON, embeds all cards)
docker compose run --rm ingest

# 4. Train (stub until ingest has run at least once)
docker compose run --rm trainer python train.py --phase 2 --epochs 20

# 5. Open UI
open https://$UI_HOST
```

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
- **EDHREC** — human deck co-occurrence data (manual download; cache in
  `ingest_cache` volume under `/data/edhrec/`).

## Database schema (key tables)

| Table              | Purpose                                      |
|--------------------|----------------------------------------------|
| `cards`            | Oracle card data from Scryfall               |
| `card_embeddings`  | Per-model vector embeddings (pgvector)       |
| `card_abilities`   | Structured ability tags (keyword/triggered)  |
| `synergy_edges`    | Pairwise synergy scores, multiple score types|
| `decks`            | Human-constructed Commander decklists        |
| `generated_decks`  | Model output decks, one row per inference    |

## Conventions

- **Python 3.12** everywhere in Python services.
- **SQLAlchemy async** with `asyncpg` in API and ingest; sync `psycopg2` in
  trainer (PyTorch DataLoader workers are synchronous).
- Embeddings dimension is **384** (all-MiniLM-L6-v2 default).  If you change
  the model, update the `vector(384)` column in the migration and add a new
  `model` row in `card_embeddings` — do not alter existing embeddings.
- The `ingest` and `trainer` services run to completion (`restart: "no"`).
  Restart them manually with `docker compose run --rm <service>`.
- Traefik TLS is handled externally; services only need the labels already in
  `docker-compose.yml`.  Do not add TLS config inside containers.
- Never commit `.env`; only commit `.env.example`.

## Phase 2 training — findings (2026-03-14)

**Run ID:** `671fpop8` (wandb project `edh-builder`, run name `lemon-spaceship-7`)

### Loss benchmarks

| Outcome | Final loss |
|---------|-----------|
| Barely learning | > 0.65 |
| Good | 0.55 – 0.60 |
| Excellent | 0.45 – 0.50 |
| Overfit risk | < 0.45 |

Epoch 1 baseline: **0.6610** (random baseline ≈ 0.693 — model is learning from batch 1).

### Infrastructure lessons learned

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

### Next steps after Phase 2

1. Evaluate final loss (epoch 20) against benchmarks above.
2. If loss > 0.65, re-run with more synergy data (increase `SYNERGY_LIMIT`) and/or
   more epochs.
3. Rebuild `synergy_edges` with a larger cap once disk space allows, then re-train.
4. Implement Phase 3 (deck co-occurrence): populate `decks` table from EDHREC data
   and train `DeckConstructor` on commander → card-set ranking.

---

## XMage as a training signal

XMage's Java card implementations encode machine-readable ability structure
(triggered, activated, static, keyword) via typed Java classes.  The ingest
pipeline's `tag_abilities` stage uses regex heuristics on oracle text as a
starting point.  A richer extraction pass can parse XMage source directly
(e.g. grep `TriggeredAbilityImpl`, `ActivatedAbilityImpl`) to produce more
precise `card_abilities` rows.  This is a planned enhancement.
