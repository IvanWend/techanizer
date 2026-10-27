# Roadmap

**Techanizer** — tech job-market analysis agent over heterogeneous vacancy sources (HN,
Remotive, Web3.career, Habr). Done-checklist; reasoning lives in `DECISIONS.md`, overview + next
step in `PROMPT.md`.

## Phase 1 — Ingestion
- [x] Four board clients — HN (Algolia), Remotive, Web3.career, Habr Career
- [x] 90-day rolling window + idempotent upsert on `(source, external_id)`
- [x] Keep-everything retention; `purge.py` no-op, guarded by `db_meta.role='live'`
- [x] Frozen eval baseline — `evals/snapshots/2026-10-27_raw.dump`
- [x] `generate_gold_dataset.py` → `gold_40_candidates.json` (byte-identical reruns)

## Phase 2 — Extraction + evals
- [x] Modules — `normalize`, `schema`, `source_adapters`, `transform`, `prompt` + DDL 005–007
- [x] Pipeline — `pending`/`build_agent`/`extract`/`persist`, chunked run, Langfuse tracing
- [x] Corpus pass — 1,098 postings → 1,973 roles; `apply_ground_truth`, `backfill.py`
- [x] Skill alias v2 — version-strip, separator fold, typo map
- [x] Eval + freeze — `eval_extraction.py` (seniority 68→94%), re-snapshot

## Phase 3 — Storage + retrieval
- [x] bge-m3 embeddings at 1024-dim
- [x] `embed.py` — `title + seniority + stack + description`, `text::vector` cast
- [x] pgvector search + HNSW index (009) + seniority/remote filters
- [x] SQL analytics — `analytics.py` (skill frequency, salary distributions)
- [x] Trend aggregates — `monthly_skill_frequency` / `monthly_salary`

## Phase 4 — Agent
- [x] Tools — `sql_query` (named-query whitelist), `vector_search`, `role_detail`
- [x] Loop — DeepSeek tool-calling, `output_type=Answer`, sequential execution
- [x] Two-stage — stage 1 tool-calling → `Payload`, stage 2 prose (DeepSeek EN / Kimi RU)
- [x] Refusal — `refused` flag + localized message; out-of-scope probes
- [x] Eval — `eval_agent.py` (59/60) + FastAPI SSE streaming

## Phase 5 — Distribution & ops
- [x] CI — ruff + mypy + pytest over pgvector + curated fixture
- [x] MCP server — `techanizer/mcp/server.py`, 3 tools, direct DB
- [x] Frontend — React + Vite + Tailwind, RU/EN, SSE, structured render
- [x] Guardrails — rate limit + spend cap + max-turns; `/ask` API key; packaging
- [x] Scheduler — `ops/scheduler.py` (`load → extract → embed`) + run-log; deploy cut (runbook)

## Phase 6 — Polish
- [x] Naming — package `techanizer`, DB identity, MCP server, Habr user-agent
- [x] README — architecture diagram, data-coverage + eval tables, limitations, RU version
- [x] Dashboard cut — `/dashboard` removed; `/ask` is the single product surface
- [x] Presentation — BPMN data-flow diagram in README (EN/RU)
- [x] Fresh ingest + extract before any demo
