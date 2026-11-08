-- 001_raw_postings.sql
-- Postings exactly as fetched, one row per (source, external_id).
-- Re-runnable: applying twice is a no-op.

BEGIN;

CREATE TABLE IF NOT EXISTS raw_postings (
    id           BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source       TEXT        NOT NULL,
    external_id  TEXT        NOT NULL,
    raw_text     TEXT        NOT NULL,
    thread_month DATE,
    ingested_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT raw_postings_source_valid
        CHECK (source IN ('hn', 'remotive', 'web3', 'habr')),

    -- Arbitrates the loader's ON CONFLICT. Same external_id from two sources is fine.
    CONSTRAINT raw_postings_source_external_id_key
        UNIQUE (source, external_id),

    -- An empty posting is a bug upstream, not data. Fail loudly at ingest.
    CONSTRAINT raw_postings_raw_text_not_blank
        CHECK (btrim(raw_text) <> ''),

    -- thread_month is a month marker, so it is always day 1.
    CONSTRAINT raw_postings_thread_month_is_month_start
        CHECK (thread_month IS NULL OR EXTRACT(DAY FROM thread_month) = 1),

    -- Every HN posting comes from a dated monthly thread; Remotive has no thread.
    CONSTRAINT raw_postings_hn_has_thread_month
        CHECK (source <> 'hn' OR thread_month IS NOT NULL)
);

COMMIT;
