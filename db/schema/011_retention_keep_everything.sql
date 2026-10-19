-- 011_retention_keep_everything.sql
-- Retention flips from 90-day-delete to keep-everything. The purge is gone;
-- freshness is now a query-time filter (storage/analytics.py + vector_search.py
-- gate on db_meta.role='live'). last_seen_at records the last time a board still
-- served the posting to us, written unconditionally by load.py. The monthly
-- rollup tables are dropped — they existed only to survive the purge, and with
-- everything kept the trend report reads history straight from the source rows.
-- Re-runnable: applying twice is a no-op.

BEGIN;

-- Add nullable, backfill from updated_at (the closest proxy we have for "last
-- seen" before the loader starts writing it every run), then pin the default
-- and NOT NULL so new inserts self-populate.
ALTER TABLE raw_postings
    ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ;

UPDATE raw_postings SET last_seen_at = updated_at
 WHERE last_seen_at IS NULL;

ALTER TABLE raw_postings ALTER COLUMN last_seen_at SET DEFAULT now();
ALTER TABLE raw_postings ALTER COLUMN last_seen_at SET NOT NULL;

DROP TABLE IF EXISTS monthly_skill_frequency;
DROP TABLE IF EXISTS monthly_salary;

COMMIT;
