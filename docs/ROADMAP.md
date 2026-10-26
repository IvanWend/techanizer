# Roadmap

A **Techanizer tech job-market analysis agent over heterogeneous vacancy sources.**

**Maintenance rule.** Current state is rewritten from scratch each session, not appended to.
Decisions go in DECISIONS.md. Nothing here restates what the code says.

## Current state (2026-10-26)

- **Techanizer market analyzer** (corpus queries + monthly analysis + RU/EN + deployed demo).
  Plan in `docs/PLAN.md`, decisions in `docs/DECISIONS.md` — this file is the done-checklist.
- **Decision (2026-10-25): free-text `/ask` is the product; the query menu was reverted.** The
  5-item menu was tested and cut — free-form querying is the point, and a fixed menu trades
  capability for correctness. The wrong-answer bug it worked around is a *refusal* failure, so the
  fix is unsupported-query refusal + tighter tools, not a menu (PLAN "Free-text agent", DECISIONS
  "Agent"). Reverted in `ab7183e`, tree restored to `e86d1d9`.
- **2026-10-26 triage (done):** small-n salary medians gated (`analytics.MIN_SAMPLE=30`, the
  `unknown` seniority bucket withholds its median too — DECISIONS "Agent"); landing hero pills
  replaced by a scope greeting; suggestion rail expanded to 3–4 ML-flavoured queries per section
  (EN/RU, only queries the four canned shapes + `vector_search` can answer). Seniority `unknown`
  (43%, HN 61%) left as-is on purpose — honest, not a bug.
- **2026-10-26 refusal (done):** stage-1 `refused` flag on `Payload`/`Answer` + code-owned
  localized message (`locale.REFUSAL`); `compose`/`stream_answer` skip stage-2 prose on refusal.
  Eval adds refusal probes `o1`/`o2`/`o3` (EN) + `ro1` (RU) graded for `refused` AND no
  tables/quotes — 12/12, in-scope subset held (no over-refusal).
- Phases 1–4 done; Phase 5 mostly done (agent eval 59/60, guardrails, packaging, MCP, frontend).
- **PLAN steps 1–4 done:** structured answer contract; retention keep-everything + 90-day window
  (purge no-op, rollups dropped, `last_seen_at`, HN all-threads, `db_meta.role='live'` gate);
  skill alias v2 (version-strip + separator fold + typo map) + `/ask` API key; scheduler entrypoint
  (`ops/scheduler.py`, `load → extract → embed`) + `ingestion_runs` run-log + full-history monthly
  trend aggregates (`monthly_skill_frequency` / `monthly_salary`).
- **PLAN step 5 done:** two-stage agent — stage 1 tool-calling → language-neutral `Payload`
  (`TableSpec` + `Quote` + `Fact`), stage 2 per-locale prose (`Prose`, numbers only as
  `{placeholders}`, code fills locale-formatted); code-owned table labels (`agent/locale.py`);
  `/ask` carries `lang`; RU eval set (`r1`…`ri1`) + cross-language grader (`x2`, same sources +
  top-1). **A/B done:** Kimi won the RU set (30/30 vs DeepSeek 27/30) and is now the RU prose
  model; stage 1 moved flash → `deepseek-v4-pro` (flash drifted on the table contract); prose
  prompt tightened (one-sentence headline, similarity never a fact).

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
- [x] Scheduled re-ingestion entrypoint — `ops/scheduler.py` (`load → extract → embed`) +
      `ingestion_runs` run-log (PLAN step 4); the cron/timer line is a step-7 deploy concern

## Phase 6 — Polish

- [ ] README with architecture diagram, eval-results table, Known limitations, demo GIF
- [ ] Cross-source dedup (fuzzy company+title) + trend charts
- [ ] Hand-roll the validate-and-retry loop to see what `pydantic-ai` hides (see DECISIONS)
- [ ] **Refactor product naming:** rename the remaining `remote-job-search` / job-search
      terminology in package metadata, CLI help, module comments, and documentation to
      Techanizer and tech job-market analysis; preserve database/table identifiers where a
      migration would add risk.

## Next

1. **Tools optimization** — tighten tool descriptions and fan-out; revisit `tool_calls_limit` and
   the flash-vs-pro stage-1 split.
2. **PLAN step 6** — monthly market analysis job (numeric payload → prose, both locales, stored in
   a `reports` table).
3. **Deploy** (Phase 5) + **README** + finish the **naming pass** (Phase 6).
