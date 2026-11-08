-- 002_posted_at.sql
-- Per-row publish timestamp, tz-aware. thread_month stays as the coarse
-- month marker (still used for HN's thread-level grouping); posted_at is
-- the actual per-posting time and is what Phase 3/4 date filters should use.
-- Re-runnable: applying twice is a no-op.

BEGIN;

ALTER TABLE raw_postings ADD COLUMN IF NOT EXISTS posted_at TIMESTAMPTZ;

-- Remotive's `publication_date` is naive (no offset) — cast straight to
-- timestamptz would silently apply the session's timezone. Interpret it as
-- UTC explicitly instead.
UPDATE raw_postings
   SET posted_at = (raw_text::jsonb ->> 'publication_date')::timestamp AT TIME ZONE 'UTC'
 WHERE source = 'remotive' AND posted_at IS NULL;

COMMIT;
