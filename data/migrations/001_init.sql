-- Enable pgvector
CREATE EXTENSION IF NOT EXISTS vector;

-- ── Cards ───────────────────────────────────────────────────────────────────
-- Source of truth is Scryfall oracle_cards bulk export.
-- oracle_id is stable across printings.
CREATE TABLE IF NOT EXISTS cards (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    oracle_id       UUID UNIQUE NOT NULL,
    name            TEXT NOT NULL,
    mana_cost       TEXT,
    cmc             NUMERIC,
    type_line       TEXT,
    oracle_text     TEXT,
    colors          TEXT[],
    color_identity  TEXT[],
    keywords        TEXT[],
    legalities      JSONB,
    produced_mana   TEXT[],
    power           TEXT,
    toughness       TEXT,
    loyalty         TEXT,
    scryfall_data   JSONB,          -- full raw blob for anything else
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cards_name ON cards (name);
CREATE INDEX IF NOT EXISTS idx_cards_color_identity ON cards USING GIN (color_identity);
CREATE INDEX IF NOT EXISTS idx_cards_keywords ON cards USING GIN (keywords);
CREATE INDEX IF NOT EXISTS idx_cards_type_line ON cards USING GIN (to_tsvector('english', coalesce(type_line,'')));
CREATE INDEX IF NOT EXISTS idx_cards_oracle_text ON cards USING GIN (to_tsvector('english', coalesce(oracle_text,'')));

-- ── Card embeddings ──────────────────────────────────────────────────────────
-- Separate table so we can swap/add embedding models without touching cards.
CREATE TABLE IF NOT EXISTS card_embeddings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    card_id         UUID NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    model           TEXT NOT NULL,          -- embedding model identifier
    embedding       vector(384),            -- dim matches all-mpnet-base-v2 (768-d after migration 004); alter per model
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (card_id, model)
);

CREATE INDEX IF NOT EXISTS idx_card_embeddings_ivfflat
    ON card_embeddings USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- ── Ability graph ────────────────────────────────────────────────────────────
-- Structured ability tags parsed from XMage source / Scryfall keywords.
-- Enables rule-based synergy signals for training.
CREATE TABLE IF NOT EXISTS card_abilities (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    card_id         UUID NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    ability_type    TEXT NOT NULL,   -- 'keyword', 'triggered', 'activated', 'static'
    ability_name    TEXT NOT NULL,   -- e.g. 'Flying', 'Dies trigger', 'Tap: add {G}'
    trigger_event   TEXT,            -- normalised event that fires this ability
    effect_class    TEXT,            -- normalised effect category
    raw_text        TEXT
);

CREATE INDEX IF NOT EXISTS idx_card_abilities_card ON card_abilities (card_id);
CREATE INDEX IF NOT EXISTS idx_card_abilities_type ON card_abilities (ability_type, ability_name);

-- ── Synergy edges ────────────────────────────────────────────────────────────
-- Pre-computed pairwise synergy scores used as training labels.
-- score_type: 'text_sim' | 'ability_trigger' | 'human_cooccurrence' | 'model'
CREATE TABLE IF NOT EXISTS synergy_edges (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    card_a      UUID NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    card_b      UUID NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    score_type  TEXT NOT NULL,
    score       FLOAT NOT NULL,
    metadata    JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (card_a, card_b, score_type)
);

CREATE INDEX IF NOT EXISTS idx_synergy_a ON synergy_edges (card_a, score_type);
CREATE INDEX IF NOT EXISTS idx_synergy_b ON synergy_edges (card_b, score_type);

-- ── Commander decks (human reference) ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS decks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    commander_id    UUID REFERENCES cards(id),
    source          TEXT NOT NULL,           -- 'edhrec', 'moxfield', 'synthetic', etc.
    source_url      TEXT,
    card_ids        UUID[] NOT NULL,         -- 99 non-commander cards
    metadata        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_decks_commander ON decks (commander_id);
CREATE INDEX IF NOT EXISTS idx_decks_source ON decks (source);

-- ── Model generations ────────────────────────────────────────────────────────
-- Output decks produced by trained model checkpoints.
CREATE TABLE IF NOT EXISTS generated_decks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    commander_id    UUID NOT NULL REFERENCES cards(id),
    checkpoint      TEXT NOT NULL,           -- model version / checkpoint path
    card_ids        UUID[] NOT NULL,
    scores          FLOAT[],                 -- per-card confidence scores
    metadata        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_generated_commander ON generated_decks (commander_id);
