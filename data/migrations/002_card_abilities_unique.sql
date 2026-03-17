-- Migration 002: add unique constraint on card_abilities for role tags
-- Needed so INSERT … ON CONFLICT DO NOTHING works in the role annotation pipeline.
--
-- Strategy: delete duplicate rows first (keep the oldest), then add the constraint.
-- If the constraint already exists this is a no-op.

-- Step 1: remove duplicates, keeping the row with the smallest id per (card_id, ability_type, ability_name, effect_class)
DELETE FROM card_abilities
WHERE id NOT IN (
    SELECT DISTINCT ON (card_id, ability_type, ability_name, COALESCE(effect_class, ''))
        id
    FROM card_abilities
    ORDER BY card_id, ability_type, ability_name, COALESCE(effect_class, ''), id
);

-- Step 2: add unique constraint (idempotent via DO $$)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_card_abilities_role_key'
    ) THEN
        ALTER TABLE card_abilities
            ADD CONSTRAINT uq_card_abilities_role_key
            UNIQUE (card_id, ability_type, ability_name, effect_class);
    END IF;
END;
$$;
