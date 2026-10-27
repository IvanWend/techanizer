# Plan — Techanizer tech job-market analyzer

**Goal.** Turn the portfolio demo into a production-grade Techanizer market analyzer:
corpus query → structured analysis with tables and verbatim quotes → monthly market analysis
→ RU/EN → deployed demo.
Built on the current stack (Postgres 17 + pgvector, pydantic-ai, FastAPI + SSE, React/Vite),
no rewrite.

Decisions here are locked for the build. Reasoning lives in DECISIONS.md; done-items stay in
ROADMAP.md.

## Product scope (what ships)

- **Query box.** Agent answers with a typed payload rendered by the UI — not free markdown.
- **Monthly analysis.** Fixed corpus analyses, generated as prose, stored, and shown in both languages.
- **RU/EN.** UI chrome, prose and tables fully localized.
- **Deployed demo.** Single VPS, docker-compose.

## Locked decisions

### Retention — keep everything
- Disable the purge; keep all rows. Add `first_seen` / `last_seen`.
- Old data exists for **trends** and **re-extraction** of corrected history. Keeping it is ~free;
  building trends now is not.
- Build only the `in_window` filter now; the trend report is the first thing to cut.

### Query scoping — hard rule
- **Live agent (`sql_query`, `vector_search`, `role_detail`): 90-day window only.**
  Add `posted_at >= now() - interval '90 days'` to the 4 `analytics` queries and `vector_search`.
  `role_detail` needs nothing (its keys come from windowed hits).
- **Trends:** report only, via monthly buckets over all history; never a live-agent concern.
- `in_window` keys on `posted_at` (not `last_seen`).

### Free-text agent (menu rejected)
- **`/ask` is free-text, not a menu.** The 5-item menu (2026-10-25) was built, tested, and reverted
  in `ab7183e` — free-form querying is the product, and a fixed menu trades capability for
  correctness. The two-stage agent stays: stage 1 tool-calling → `Payload`, stage 2 prose.
- **Unsupported queries must refuse, not answer wrong.** The 1v/2v failures were a *refusal*
  failure — "junior AI skills" returned the corpus-wide skill table, silently ignoring `junior` (a
  real column) and `AI` (no column). The agent refuses out-of-scope queries; an honest "can't
  answer" beats a confident wrong answer.
- **Tools optimization, not a menu.** Constrain the tools so the model can't go wrong *or* waste
  calls: the named-query `Literal` (no SQL crosses the boundary), code-ranked `top_salaries` (no
  free-styled sort), sequential execution, usage limits. Remaining: tighten tool descriptions
  (flash drifted on the exact-cells contract) and cap fan-out.

### Privacy
- Skip email redaction at ingestion. Consequence on record: HN `raw_text` still contains
  emails/contact, surfaceable via `role_detail`/MCP → note in README "Known limitations".

### Data quality
- Skill alias v2 only. The one DQ fix kept; it affects every answer (skill-frequency
  bars, `vector_search`, `role_detail` all read the stored `stack`).
  - **version-strip** — drop a trailing version token, space-gated so a no-space token
    (`ec2`, `html5`, `c++23`) survives: `java 17+`→`java`, `python 3.10+`→`python`.
  - **separator fold** — whitespace/underscore/hyphen runs collapse to one `-`
    (`rest api`/`rest_api`/`rest-api`→`rest-api`). Slash and dot stay literal: a slash is
    ambiguous (`ci/cd` is one skill, `docker/k8s` is two) and a dot anchors node.js/react.js.
  - **typo tolerance** — the explicit `STACK_ALIASES` map, not edit-distance; seeded from
    corpus typos (`postgesql`/`postgress`→`postgres`, `oauth2`→`oauth`, `restapi`→`rest-api`).

### API key
- `/ask` is gated by a shared key (`ASK_API_KEY`). Unset → endpoint open (dev + CI); set →
  every request needs `Authorization: Bearer <key>` (constant-time compare). `/health` and the
  static mount stay open. The SPA sends `VITE_ASK_API_KEY` (from `frontend/.env`) as the header;
  a key in the browser bundle is readable to anyone who loads the page — shared-key, not
  per-user auth — documented in README "Known limitations".

### Answer contract
- The agent returns a typed object; the UI renders it and nothing else — no free markdown, no
  inference from tool results:
  - `headline` — one-sentence direct answer.
  - `key_finding` — strongest numeric claim, cites quotes as `[n]`.
  - `tables` (≤3) — `{kind: bars|table, title, headers?, rows}`; `bars` = skill frequency
    (≤10 rows), `table` = anything else (headers + ≤10 rows). Numbers copied as the tools
    returned them: monthly, own currency, never converted.
  - `quotes` (≤5) — `{id, company, title, source, seniority?, stack?, salary_min/max/currency?,
    text}`; `text` is a verbatim contiguous substring of the posting (`source_quotes`/raw text),
    the other fields copied from the structured record. Carries id **and payload** (supersedes
    the earlier `quote_ids` wording): the UI needs the text in the same frame, and evals verify
    verbatimness against the DB.
- Prompt rules: tables and quotes only from tool output; posting text is data — instructions
  inside it are ignored (absorbs the untrusted-delimiting item); caps as above.
- UI: contract-only render. `headline` → h1, `key_finding` → callout with citation chips,
  `tables` → bars/tables, `quotes` → evidence cards (≤8 skill chips then "+N", explicit
  "salary not stated" instead of silence). The tool-result panels and the call/result pairing
  are deleted with it. Inline skill highlighting stays: vocabulary = quotes' stacks ∪ bars'
  first column, matched through the surface-form dictionary.
- RU/EN: chrome now; prose stays EN until the stage-2 locale pass (step 5).

### Monthly report
- Two scopes in one payload:
  - **current** — windowed top skills + salary-by-seniority (USD-stated).
  - **trend** — monthly buckets over history; a month with `<30 postings` ⇒ refuse and say so.
- Store the locale-neutral numeric payload + prose per locale in a `reports` table.

### Localization
- UI fully RU/EN (chrome + prose + tables).
- Numbers flow through placeholders (`{n_roles}`, `{median_usd}`); code fills locale-formatted
  values (`1,604` vs `1 604`, `24.5%` vs `24,5%`). Reject prose with literal digits.

### Models
- CN open-weight only (DeepSeek / Qwen / Kimi), one account.
- Two stages: stage 1 tool-calling → language-neutral payload; stage 2 prose by locale from config.
- A/B a RU model after ~10 RU questions on DeepSeek; DeepSeek is the fallback.

### Evals
- Existing suite + Russian set + README results table + `/ask` API key.
- Graders take the `Answer` object: numeric graders read `tables` and the prose ints; top-salary
  graders match `analytics.rank_top_salaries` company/title in quotes or table rows; **new
  verbatim grader** checks every `Quote.text` is a substring of that role's `source_quotes`/raw
  text and company/title match the structured row; injection checks headline/key_finding only
  (quotes are verbatim-grounded, so they're exempt).
- Cross-language grader = same EN/RU question returns the same `tables` payload (replaces the
  flaky `x3`).
- **Out-of-scope probes must refuse** — the current set has none, so it cannot regress the
  wrong-answer bug. Add questions the agent must refuse (off-corpus entities, columns that don't
  exist) and grade them for refusal.

### Ops
- Weekly re-ingestion (`load → extract → embed`) + monthly market analysis job.
- Run-log table (per source: start, end, rows, errors).
- HN loader ingests **all** in-window threads (not just the latest).

## Dropped (do not do)

Rollup fix, purge guard (obsolete), FX conversion, cross-source dedup, automated backups, full CI
eval gate, reproducibility columns, email redaction, a 5th source, resume-matching, hand-rolled
retry loop, `currency_enum` rework (already a strict whitelist), monthly report (duplicates the
canned queries the agent already answers; trend half needs ≥6 months of history).

## Build order (~7 weeks)

| # | Work | Days |
|---|---|---|
| 1 | Structured answer contract: schema → prompt → SSE frame → UI contract render → eval-grader rewrite | 1–8 |
| 2 | Retention migration + `in_window` filters (`analytics.py` + `vector_search.py`) + `last_seen` + HN all-threads | 9–12 |
| 3 | Skill alias v2 + `/ask` API key | 13–17 |
| 4 | Scheduler + run-log + live monthly aggregates (remove rollups/purge) | 18–21 |
| 5 | RU-model A/B + placeholder fidelity + same-tables grader + stage-2 prose locales | 22–27 |
| 5a | Unsupported-query refusal + tools optimization (stage-1 prompt + out-of-scope probes) | follow-up |
| 6 | Monthly market analysis job — **cut** (replaced by live `/dashboard` endpoint + Dashboard tab; the stored report duplicates canned queries the agent already answers, and the trend half needs ≥6 months of history) | 28–34 |
| 7 | Deploy + README (results table + architecture + known limitations) + slides | 35–42 |
| 8 | Techanizer naming refactor — **done** (2026-10-27) | follow-up |

**Cut first if time slips:** monthly analysis *trend* half → scheduler → RU second model (DeepSeek
prose both locales).

**Never cut:** retention migration + window filters, skill aliases, structured answer, `/ask` API key.

## File touch-points

- `ingestion/retention.py` — keep `WINDOW_DAYS` as the "current" window; purge path gone.
- `ingestion/purge.py` — disabled (no-op).
- `ingestion/load.py` — unconditional `last_seen_at` write; HN fetch all in-window threads.
- `db/schema/` — new migration for `first_seen`/`last_seen`; rollup tables dropped.
- `storage/analytics.py` — date filter on all 4 queries.
- `storage/vector_search.py` — date filter on `SEARCH_SQL`.
- `extraction/normalize.py` — skill alias v2.
- `agent/schema.py`, `agent/prompt.py` — `Table`/`Quote`/`Answer` models; contract + untrusted-data
  rules in the prompt.
- `agent/loop.py` — CLI prints headline/key_finding instead of free text.
- `serving/sse.py` — answer frame = `Answer.model_dump()`; `serving/app.py` — API-key auth.
- `storage/role.py` — `source_quotes` feed `Quote.text` verbatim.
- `evals/eval_agent.py` — graders over the contract + verbatim-quote + same-tables cross-language.
- `frontend/` — contract-only render + RU/EN i18n; tool-result panels and step pairing deleted;
  surface-form dictionary for inline highlighting; caps + explicit empty states.
- new — report generation module, scheduler/run-log.
