-- 004_source_check.sql
-- Repoints the allowed source list at the remote-only lineup:
-- Re-runnable: applying twice is a no-op.

BEGIN;

ALTER TABLE raw_postings DROP CONSTRAINT IF EXISTS raw_postings_source_valid;

ALTER TABLE raw_postings ADD CONSTRAINT raw_postings_source_valid
    CHECK (source IN ('hn', 'remotive', 'web3', 'habr'));

COMMIT;
