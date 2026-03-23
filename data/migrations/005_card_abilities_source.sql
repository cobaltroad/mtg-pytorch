-- Migration 005: add source column to card_abilities
-- Tracks where each ability row came from: 'pattern' (oracle-text regex) or
-- 'xmage' (XMage Java source parsing).  Existing rows default to 'pattern'.

ALTER TABLE card_abilities
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'pattern';

-- Back-fill existing rows so they carry the explicit label.
UPDATE card_abilities SET source = 'pattern' WHERE source = 'pattern';
