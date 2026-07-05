-- Popularity prior for heuristic pool ranking (issue #140).
-- MTGJSON edhrecRank: 1 = most-played card in EDH; NULL = unranked
-- (brand-new or unplayed cards).  Populated by the download stage.

ALTER TABLE cards ADD COLUMN IF NOT EXISTS edhrec_rank INTEGER;
