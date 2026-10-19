# Roadmap

A **remote job search agent over heterogeneous boards.**

**Maintenance rule.** Current state is rewritten from scratch each session, not appended to.
Decisions go in DECISIONS.md. Nothing here restates what the code says.

## Current state (2026-10-19)

- **Production labor-market analyzer** (query box + monthly report + RU/EN + deployed demo).
  Plan in `docs/PLAN.md`, decisions in `docs/DECISIONS.md` — this file is the done-checklist.
- Phases 1–4 done; Phase 5 mostly done (agent eval 59/60, guardrails, packaging, MCP, frontend).
- **PLAN steps 1–3 done:** structured answer contract; retention keep-everything + 90-day window
  (purge no-op, rollups dropped, `last_seen_at`, HN all-threads, `db_meta.role='live'` gate);
  skill alias v2 (version-strip + separator fold + typo map) + `/ask` API key.

## Phase 1 — Ingestion

- [x] Four board clients: HN (Algolia), Remotive, Web3.career, Habr Career
- [x] 90-day rolling window filter + idempotent upsert on `(source, external_id)` — `load.py`
- [x] `retention.py` + `purge.py`, guarded by `db_meta.role='live'` (see GOTCHAS: psycopg / Docker)
- [x] Frozen eval baseline: `evals/snapshots/2026-08-12_raw.dump` → `jobmarket_eval`, `jobmarket_ro`
- [x] `evals/generate_gold_dataset.py` → `gold_40_candidates.json`, seeded, reruns byte-identical

## Phase 2 — Extraction + evals

- [x] Extraction modules — `normalize`, `source_adapters`, `schema`, `transform`, `prompt`, DDL 005–007
- [x] Pipeline — `pending`/`build_agent`/`extract`/`persist`, chunked `run`, Langfuse tracing
- [x] Corpus pass + repairs — pilot, then all 1,098 rows → 1,663 roles; `apply_ground_truth`, `backfill.py`
- [x] Tests — `test_normalize` / `test_transform` / `test_source_adapters` / `test_schema` + fixtures
- [x] Eval + freeze — `gold_labeled.json`, `eval_extraction.py` (prompt 2×, seniority 68→93%), re-snapshot → `jobmarket_eval`

## Phase 3 — Storage + retrieval

- [x] Model — bge-m3 at 1024-dim (verified live)
- [x] Embedding pipeline — `embed.py`: `title + seniority + stack + description`, `text::vector` cast (DECISIONS: storage)
- [x] pgvector search + basic metadata filters (seniority, remote) — `vector_search.py`, HNSW index (009)
- [x] SQL analytics queries (skill frequency, salary distributions) — hand-written — `analytics.py`
- [x] Monthly rollups so trends survive the purge (see DECISIONS: storage) — `rollups.py` + DDL 010
      (dropped in step 2: keep-everything makes them redundant)

## Phase 4 — Agent

- [x] Tools: `sql_query`, `vector_search`, `role_detail` (`resume_match` → `role_detail`)
- [x] Agent loop with DeepSeek tool-calling (`output_type=Answer`, sequential tool execution)
- [x] Agent eval suite — `evals/eval_agent.py`: 18 questions + scorer, 3× runs; baseline 45/45 on
      the original 15 (before the provider/model swap), then +d9/x2/x3 for the salary-ranking fix
- [x] FastAPI endpoint with SSE streaming

## Phase 5 — Distribution & ops

- [x] CI — ruff + mypy + pytest over pgvector + curated fixture (`export_ci_fixture.py`)
- [x] MCP server — `jobmarket_agent/mcp/server.py` (FastMCP 1.29), 3 tools, direct DB
- [x] Frontend — Techanizer UI (React + Vite + Tailwind v4, Figma design merged): query box,
      Analysis/Monthly tabs, RU/EN chrome, dark/light; renders structured tool output +
      highlighted prose; hand-written SSE client; served from FastAPI `StaticFiles` mounted only
      when `dist/` exists (CI has no build)
- [x] Rate limit + spend cap + max-turns on the agent loop (before public, non-negotiable)
- [x] Packaging — installable (hatchling + `jobmarket_agent` flat layout); see DECISIONS: Packaging
- [x] Fresh ingest + extract right before deploy (demo freshness)
- [ ] Deployment — single VPS + docker-compose (`web` + `ollama`); provider + budget TBD;
      run the agent eval before/after the provider/model swap
- [ ] Scheduled re-ingestion — weekly cron (PLAN step 4)

## Phase 6 — Polish

- [ ] README with architecture diagram, eval-results table, Known limitations, demo GIF
- [ ] Cross-source dedup (fuzzy company+title) + trend charts
- [ ] Hand-roll the validate-and-retry loop to see what `pydantic-ai` hides (see DECISIONS)

## Next

1. Follow `docs/PLAN.md` build order, step 4: scheduler + run-log + live monthly aggregates.
