# Techanizer

Techanizer is an applied-AI tech job-market agent. It analyzes a corpus of remote
technology vacancies from four sources: Hacker News *Who Is
Hiring*, Remotive, Web3.career, and Habr Career.

It turns inconsistent English and Russian postings
into structured, searchable data and answers questions such as:

- Which skills are most common among backend roles?
- What salary ranges are reported by currency or seniority?
- Which vacancies are semantically closest to a role description?

This is a production-oriented project for integrating AI into a real data
pipeline. The source mix is intentionally heterogeneous; it is not intended to be
a statistically representative model of the whole labor market. Techanizer answers questions about the corpus and provides a live market
dashboard.

## What is implemented

```text
Public board endpoints
        │
        ▼
Postgres raw postings ──► LLM extraction with Pydantic validation
        │                         │
        │                         ▼
        └──────────────► structured roles + grounded quotes
                                  │
                    Postgres + pgvector + bge-m3 embeddings
                                  │
             DeepSeek tool-calling agent / MCP tools / FastAPI API
                                  │
                         React + Vite frontend
```

- Four ingestion adapters with idempotent upserts and source-specific parsing.
- Rolling 90-day live search window while preserving historical data for trends.
- Structured extraction of company, title, skills, seniority, remote policy,
  employment type, salary, and source quotes.
- Skill normalization and aliasing across inconsistent source vocabulary.
- PostgreSQL, pgvector, HNSW vector search, and local `bge-m3` embeddings.
- Typed two-stage agent: tool-calling produces a language-neutral payload; a
  second model writes English or Russian prose.
- SQL analytics, semantic search, role details, SSE streaming, API-key gating,
  rate limiting, token limits, and MCP access.
- React UI with English/Russian output, light/dark themes, structured tables,
  quotes, and explicit missing-data states.
- Weekly `load → extract → embed` scheduler with per-source run logging.


## Try it locally

### Requirements

- Python 3.13, [uv](https://docs.astral.sh/uv/), and Docker Compose
- PostgreSQL client (`psql`)
- A DeepSeek API key
- Ollama with the `bge-m3` embedding model
- A Web3.career API token if that source is enabled

### Install and configure

```bash
uv sync
cp .env.example .env
# Fill in DEEPSEEK_API_KEY, DATABASE_URL, and other required values.
set -a; . ./.env; set +a
```

Start PostgreSQL and apply the schema:

```bash
docker compose up -d
for f in db/schema/*.sql; do
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"
done
```

Load, extract, and embed postings:

```bash
uv run python -m jobmarket_agent.ingestion.load
uv run python -m jobmarket_agent.extraction.pipeline
uv run python -m jobmarket_agent.storage.embed
```

Or run the scheduled pipeline as one command:

```bash
uv run python -m jobmarket_agent.ops.scheduler
```

Start the API and UI:

```bash
uv run uvicorn jobmarket_agent.serving.app:app
```

Open `http://localhost:8000`. The API also exposes:

```bash
curl http://localhost:8000/health
curl --get 'http://localhost:8000/ask' \
  --data-urlencode 'q=What skills are common among senior backend roles?' \
  --data-urlencode 'lang=en'
```

If `ASK_API_KEY` is set, add `-H "Authorization: Bearer $ASK_API_KEY"` to the
second command.

The browser key is intentionally only a shared demo gate, not user
authentication. It is visible to anyone who can load the frontend.

## MCP access

The same three database-backed tools are available through a stdio MCP server:

```bash
uv run python -m jobmarket_agent.mcp.server
```

Configure the MCP host with absolute paths and put `DATABASE_URL` and
`OLLAMA_BASE_URL` in the host's `env` block. The MCP server does not require a
DeepSeek key because the host supplies the model.

## Project layout

```text
jobmarket_agent/ingestion/    board clients and loading
jobmarket_agent/extraction/   schemas, prompts, normalization, pipeline
jobmarket_agent/storage/      SQL analytics, embeddings, vector search
jobmarket_agent/agent/        tools, schemas, localization, model loop
jobmarket_agent/serving/      FastAPI and SSE streaming
jobmarket_agent/mcp/          FastMCP server
jobmarket_agent/ops/           scheduler and ingestion run log
frontend/                     React/Vite/Tailwind UI
db/schema/                    re-runnable database migrations
tests/                        unit, contract, serving, and integration tests
evals/                        frozen snapshots, gold data, and evaluations
docs/                         roadmap, plan, and engineering decisions
```

## Scope and limitations

- The corpus focuses on remote technology jobs and is biased toward startup,
  Web3, Hacker News, Remotive, and Habr communities.
- Three sources are primarily English and one is Russian. This reflects legal
  source availability and provides a multilingual extraction/use case; it does
  not represent national language proportions.
- Currency values are kept in their stated currency and are not converted.
- Contact information in raw postings can be exposed through detailed record
  access.

## Validation

```bash
uv run ruff check jobmarket_agent/ evals/ tests/
uv run mypy jobmarket_agent/ tests/
uv run pytest -q
cd frontend && npm run lint && npm run build
```
