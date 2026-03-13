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

## XMage as a training signal

XMage's Java card implementations encode machine-readable ability structure
(triggered, activated, static, keyword) via typed Java classes.  The ingest
pipeline's `tag_abilities` stage uses regex heuristics on oracle text as a
starting point.  A richer extraction pass can parse XMage source directly
(e.g. grep `TriggeredAbilityImpl`, `ActivatedAbilityImpl`) to produce more
precise `card_abilities` rows.  This is a planned enhancement.
