# Techanizer

An applied-AI tech job-market analyst. Techanizer ingests remote technology
vacancies from four heterogeneous boards — Hacker News *Who Is Hiring*, Remotive,
Web3.career, and Habr Career — extracts them into structured, searchable records
with verbatim grounding quotes, and answers free-text questions about the corpus
through a tool-calling agent.

This is a production-oriented demonstration of integrating an LLM into a real
data pipeline: ingestion → structured extraction → vector storage → a typed,
tool-calling agent → a localized UI. The source mix is intentionally
heterogeneous; it is not a statistically representative model of the whole labor
market, and the agent answers *about the corpus*, refusing questions the data
cannot support.

## Architecture

```mermaid
flowchart LR
    subgraph SRC["Sources"]
        hn["Hacker News (Algolia)"]
        rem["Remotive (API)"]
        w3["Web3.career (API)"]
        hab["Habr Career (API)"]
    end
    subgraph INGEST["Ingestion"]
        raw[("Postgres raw postings<br/>idempotent upsert")]
    end
    subgraph EXTRACT["Extraction"]
        ext["LLM extraction + Pydantic validation<br/>(DeepSeek V4.1 Flash)<br/>→ structured roles + verbatim quotes"]
    end
    subgraph STORE["Storage"]
        db[("Postgres + pgvector<br/>bge-m3 (1024-dim)")]
    end
    subgraph QUERY["Query"]
        sql["SQL analytics<br/>(skill freq, salary)"]
        vec["Hybrid search<br/>(pgvector + tsvector, RRF)"]
    end
    subgraph AGENT["Agent"]
        agent["Two-stage agent<br/>(GLM 5.3 → Qwen Flash)<br/>tool-calling → payload → prose"]
    end
    subgraph SERVE["Serving"]
        ui["FastAPI + SSE · MCP server · React UI"]
    end

    hn & rem & w3 & hab --> raw
    raw --> ext
    ext --> db
    db --> sql & vec
    sql & vec --> agent
    agent --> ui
```


## Models

| Stage | Model |
|---|---|
| Extraction | DeepSeek V4.1 Flash (Alibaba) |
| Agent stage 1 (tool-calling) | GLM 5.3 (Alibaba) |
| Agent stage 2 (prose) | Qwen 3.8 Flash (Alibaba, EN + RU) |
| Embeddings | Qwen3 text-embedding-v4 (Alibaba, 1024-dim) |
| Eval judge | GLM 5.3 (Alibaba) |


## Evaluation

Evaluations run against a frozen, byte-identical snapshot of the structured
corpus (`evals/snapshots/`), so results are reproducible and independent of the
live board.

- **Extraction accuracy** (40 gold-labeled postings, exact-match on discrete
  fields): seniority 94%, employment type 96%, remote policy 96%.
- **Grounding audit** (12,598 extracted values): 97% verbatim; the remainder are
  stitched bullet fragments, not fabrications.
- **Agent eval** (deterministic, semantic, refusal, injection, and cross-language
  probes, 3 runs each): 59/60 on the frozen snapshot.
- **Retrieval quality** (12 labeled queries, lexical ground truth): mean MRR 1.00,
  precision@10 0.92 (Qwen3 text-embedding-v4). Hybrid dense + lexical (tsvector) legs fused by
  reciprocal rank. bge-m3 baseline: MRR 0.96, precision@10 0.73.
- **Tests:** 253 unit/contract/serving/integration tests, plus the above LLM
  evals.

## Try it locally

### Requirements

- Python 3.13, [uv](https://docs.astral.sh/uv/), and Docker Compose
- PostgreSQL client (`psql`)
- An Alibaba Cloud (DashScope) API key
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

Load, extract, and embed postings (or run all three as one scheduled command):

```bash
uv run python -m techanizer.ingestion.load
uv run python -m techanizer.extraction.pipeline
uv run python -m techanizer.storage.embed

# equivalent one-shot scheduler:
uv run python -m techanizer.ops.scheduler
```

Start the API and UI:

```bash
uv run uvicorn techanizer.serving.app:app
```

Open `http://localhost:8000`. The API also exposes:

```bash
curl http://localhost:8000/health
curl --get 'http://localhost:8000/ask' \
  --data-urlencode 'q=What skills are common among senior backend roles?' \
  --data-urlencode 'lang=en'
```

If `ASK_API_KEY` is set, add `-H "Authorization: Bearer $ASK_API_KEY"` to the
second command. The browser key is intentionally only a shared demo gate, not
user authentication — it is visible to anyone who can load the frontend.

## MCP access

The same three database-backed tools are available through a stdio MCP server:

```bash
uv run python -m techanizer.mcp.server
```

Configure the MCP host with absolute paths and put `DATABASE_URL` and
`ALIBABA_API_KEY` in the host's `env` block — the `vector_search` tool embeds
the query, so it needs the model key even though the SQL tools do not.

## Project layout

```text
techanizer/ingestion/    board clients and loading
techanizer/extraction/   schemas, prompts, normalization, pipeline
techanizer/storage/      SQL analytics, embeddings, hybrid search
techanizer/agent/        tools, schemas, localization, model loop
techanizer/serving/      FastAPI and SSE streaming
techanizer/mcp/          FastMCP server
techanizer/ops/          scheduler and ingestion run log
frontend/                     React/Vite/Tailwind UI
db/schema/                    re-runnable database migrations
tests/                        unit, contract, serving, and integration tests
evals/                        frozen snapshots, gold data, and evaluations
docs/                         roadmap and engineering decisions
```

The installable package and its import name are both `techanizer`.

## Scope and limitations

- The corpus focuses on remote technology jobs and is biased toward startup,
  Web3, Hacker News, Remotive, and Habr communities.
- **Salary coverage is low (32%).** Most postings simply do not state pay; the
  agent reports this rather than estimating.
- **Seniority is unknown for 43% of roles** (HN 61%): the extractor reads stated
  levels only and refuses to guess.
- Currencies are kept in their stated currency and never converted.

## Validation

```bash
uv run ruff check techanizer/ evals/ tests/
uv run mypy techanizer/ tests/
uv run pytest -q
cd frontend && npm run lint && npm run build
```
