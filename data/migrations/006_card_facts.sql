-- Composition-first builder, Layer 1 (docs/composition-first-plan.md, W1).
--
-- 1. cards.faces — the full MTGJSON faces list for multi-face cards.
--    download.py previously kept only face[0]; MDFC land detection needs
--    the back face's type line.
-- 2. card_facts — structured, queryable mana profile + land classification
--    per card.  Rebuilt by `pipeline.py --stage compute_card_facts`;
--    derivation logic lives in shared/composition/card_facts.py.

ALTER TABLE cards ADD COLUMN IF NOT EXISTS faces JSONB;

CREATE TABLE IF NOT EXISTS card_facts (
    card_id       UUID PRIMARY KEY REFERENCES cards(id) ON DELETE CASCADE,

    -- Mana profile (parsed from cards.mana_cost)
    generic       INT     NOT NULL DEFAULT 0,      -- sum of numeric symbols ({2} → 2)
    has_x         BOOLEAN NOT NULL DEFAULT FALSE,  -- {X}/{Y}/{Z} in cost
    pips          JSONB   NOT NULL DEFAULT '{}'::jsonb,
                  -- strict pip counts, e.g. {"W": 2, "C": 1}; keys W U B R G C S
    hybrid_pips   JSONB   NOT NULL DEFAULT '[]'::jsonb,
                  -- one entry per flexible symbol, each a list of payment
                  -- options: {W/U} → ["W","U"]; {2/W} → ["2","W"];
                  -- {B/P} → ["B","P"] (P = pay 2 life)

    -- Land classification (parsed from type_line / oracle_text / produced_mana / faces)
    is_land       BOOLEAN NOT NULL DEFAULT FALSE,  -- front face is a land
    is_basic      BOOLEAN NOT NULL DEFAULT FALSE,
    land_colors   TEXT[]  NOT NULL DEFAULT '{}',   -- WUBRG(C) this land can produce
    etb_tapped    TEXT,                            -- lands only: 'always' | 'conditional' | 'untapped'
    is_fetch      BOOLEAN NOT NULL DEFAULT FALSE,  -- sacrifices to search for land(s)
    is_mdfc_land  BOOLEAN NOT NULL DEFAULT FALSE,  -- modal DFC with a land face

    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_card_facts_land ON card_facts (is_land) WHERE is_land;
