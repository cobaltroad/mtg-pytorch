-- Migration 004: upgrade card_embeddings from 384-d (MiniLM) to 768-d (all-mpnet)
--
-- all-mpnet-base-v2 produces 768-d vectors; the existing vector(384) column
-- cannot store them.  Since we are doing a full re-embed of all cards with the
-- new model, the existing MiniLM embeddings are superseded and can be cleared.
--
-- Steps:
--   1. Drop the old IVFFlat index (tied to vector(384))
--   2. Truncate card_embeddings (MiniLM rows are replaced by the re-embed run)
--   3. Alter column type to vector(768)
--   4. Rebuild index as HNSW — better recall than IVFFlat for <1M vectors and
--      does not require a minimum row count before creation

DROP INDEX IF EXISTS idx_card_embeddings_ivfflat;

TRUNCATE TABLE card_embeddings;

ALTER TABLE card_embeddings
    ALTER COLUMN embedding TYPE vector(768);

CREATE INDEX idx_card_embeddings_hnsw
    ON card_embeddings USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

COMMENT ON COLUMN card_embeddings.embedding IS
    'Embedding vector — 768-d for all-mpnet-base-v2 (upgraded from 384-d MiniLM in migration 004)';
