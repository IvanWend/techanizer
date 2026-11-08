-- 003_retention.sql
-- Groundwork for the rolling 90-day window.
--   1. db_meta: one row saying whether this database is the live rolling corpus
--      or a restored eval snapshot. The purge asserts role = 'live' before it
--      deletes anything, so pointing it at the frozen eval DB is a no-op error
--      instead of a silent wipe of the eval baseline.
--   2. posted_at NOT NULL: the purge filters on posted_at, and
--      `WHERE posted_at < now() - interval '90 days'` silently skips NULLs —
--      those rows would live forever. Verified 0 NULLs before applying.
-- Re-runnable: applying twice is a no-op.

BEGIN;

-- singleton BOOLEAN PRIMARY KEY CHECK (singleton) allows exactly one row:
-- the only value that satisfies the CHECK is TRUE, and the PK makes it unique.
CREATE TABLE IF NOT EXISTS db_meta (
    singleton  BOOLEAN     PRIMARY KEY DEFAULT TRUE,
    role       TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT db_meta_is_singleton CHECK (singleton),

    CONSTRAINT db_meta_role_valid CHECK (role IN ('live', 'eval'))
);

-- A database that has this file applied is the live one until something says
-- otherwise; the eval restore flips it with UPDATE db_meta SET role = 'eval'.
INSERT INTO db_meta (role) VALUES ('live')
ON CONFLICT (singleton) DO NOTHING;

ALTER TABLE raw_postings ALTER COLUMN posted_at SET NOT NULL;

COMMIT;
