-- Feedback loop, phase 1 (issue #180; design: docs/feedback-loop-design.md).
--
-- Card-level votes on generated decks.  'fit' votes become supervised
-- (commander, card, label) pairs for within-slot ranking (#181); 'slot'
-- votes are pool-membership disputes routed to a rule-fix report (#182).
--
-- Aggregation is per (commander, card): the training label is
-- sign(sum(vote)) with |sum| as confidence weight.  deck_ref scopes the
-- uniqueness so re-voting the same card in the same deck updates rather
-- than stacks.
--
-- NOTE: one legacy decks.metadata['votes'] blob exists (archetype-level
-- votes from the retired 2026-03 design) — intentionally NOT migrated;
-- the archetype vote vocabulary has no equivalent here.

CREATE TABLE IF NOT EXISTS card_votes (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    commander_id  UUID NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    card_id       UUID NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    vote          SMALLINT NOT NULL CHECK (vote IN (-1, 1)),
    kind          TEXT NOT NULL DEFAULT 'fit' CHECK (kind IN ('fit', 'slot')),
    slot          TEXT,                          -- slot at vote time (context)
    deck_ref      TEXT,                          -- generated-deck filename
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (commander_id, card_id, kind, deck_ref)
);

CREATE INDEX IF NOT EXISTS idx_card_votes_commander ON card_votes (commander_id);
