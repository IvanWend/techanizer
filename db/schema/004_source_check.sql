-- 004_source_check.sql
-- Repoints the allowed source list at the remote-only lineup (Phase 1b):
-- adzuna is cut, web3 and habr are added.
--
-- ORDER MATTERS: Postgres validates a new CHECK against existing rows, so this
-- file fails while any adzuna row is still in the table. Run the purge first
-- (python -m techanizer.ingestion.purge). Adzuna's rows are not lost — they live on in
-- evals/snapshots/*.dump and stay valid as eval inputs.
-- Re-runnable: applying twice is a no-op.

BEGIN;

ALTER TABLE raw_postings DROP CONSTRAINT IF EXISTS raw_postings_source_valid;

ALTER TABLE raw_postings ADD CONSTRAINT raw_postings_source_valid
    CHECK (source IN ('hn', 'remotive', 'web3', 'habr'));

COMMIT;
