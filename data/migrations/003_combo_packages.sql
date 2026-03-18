-- Migration 003: combo_packages + combo_package_cards
-- Stores Commander Spellbook variant data for package-aware deck scoring.

CREATE TABLE IF NOT EXISTS combo_packages (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    spellbook_id          TEXT UNIQUE NOT NULL,       -- variant.id from Spellbook API
    combo_ids             INTEGER[],                  -- variant.of[].id (parent combo IDs)
    identity              TEXT NOT NULL,              -- color identity, e.g. "UB", "WUBRG"
    produces              TEXT[] NOT NULL,            -- feature names e.g. {"Infinite mana","Win the game"}
    description           TEXT,
    easy_prerequisites    TEXT,
    notable_prerequisites TEXT,
    mana_needed           TEXT,
    mana_value_needed     INTEGER,
    popularity            INTEGER,
    bracket_tag           TEXT,                       -- R|S|P|O|C|E|B
    legal_commander       BOOLEAN NOT NULL DEFAULT TRUE,
    spoiler               BOOLEAN NOT NULL DEFAULT FALSE,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS combo_package_cards (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    combo_package_id     UUID NOT NULL REFERENCES combo_packages(id) ON DELETE CASCADE,
    card_id              UUID REFERENCES cards(id) ON DELETE SET NULL,  -- NULL if not in our DB
    spellbook_card_name  TEXT NOT NULL,
    oracle_id            UUID,                        -- matched from cards.oracle_id on import
    must_be_commander    BOOLEAN NOT NULL DEFAULT FALSE,
    quantity             INTEGER NOT NULL DEFAULT 1,
    zone_locations       TEXT[],                      -- e.g. {"H"} = hand, {"B"} = battlefield
    battlefield_state    TEXT,
    is_template          BOOLEAN NOT NULL DEFAULT FALSE,   -- TRUE for requires[] generic slots
    template_name        TEXT,
    UNIQUE (combo_package_id, spellbook_card_name)
);

CREATE INDEX IF NOT EXISTS idx_cpc_card_id   ON combo_package_cards (card_id);
CREATE INDEX IF NOT EXISTS idx_cpc_oracle_id ON combo_package_cards (oracle_id);
CREATE INDEX IF NOT EXISTS idx_cpc_combo     ON combo_package_cards (combo_package_id);
CREATE INDEX IF NOT EXISTS idx_cp_identity   ON combo_packages (identity);
CREATE INDEX IF NOT EXISTS idx_cp_legal      ON combo_packages (legal_commander);
