-- 012_ingestion_runs.sql
-- One row per (run, source): the paper trail for a scheduled re-ingestion.
-- A run stamps one UUID across all four sources, so "did this week's run
-- succeed?" is a single GROUP BY. ended_at and the counts stay NULL when a
-- source threw before producing stats (error carries the message instead).
-- Re-runnable: applying twice is a no-op.

BEGIN;

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id         BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id     UUID        NOT NULL,
    source     TEXT        NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at   TIMESTAMPTZ,
    fetched    INT,
    inserted   INT,
    updated    INT,
    unchanged  INT,
    failed     INT,
    error      TEXT
);

CREATE INDEX IF NOT EXISTS ingestion_runs_run_id_idx ON ingestion_runs (run_id);

COMMIT;
