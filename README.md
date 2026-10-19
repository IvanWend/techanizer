# Remote Job Search Agent

A **remote job search agent over heterogeneous boards.** It ingests tech job postings, extracts
structured data from messy prose with an LLM, stores it in **Postgres + pgvector**, and answers
analytical questions — *"what skills are trending for backend roles"*, *"which postings match this
résumé"*.

## How it works

```
INGESTION    HN (Algolia) + Remotive + Web3.career + Habr Career ──►  raw_postings (Postgres)
RETENTION    keep-everything; 90-day window is a query-time filter only
EXTRACTION   LLM + Pydantic schema, grounded by verbatim quotes ──►  structured_postings
STORAGE      Postgres + pgvector; local bge-m3 embeddings (1024-dim) ──►  posting_embeddings
AGENT        DeepSeek tool-calling loop: sql_query · vector_search · role_detail
MCP          same three tools over stdio — no DeepSeek key
SERVING      FastAPI (SSE streaming) · Langfuse tracing
FRONTEND     React + Vite + Tailwind SPA, served from FastAPI StaticFiles
```

Four boards, remote only. Corpus size is not the goal — everything is kept, and the live agent only
reads the last 90 days. Schema and extraction rules live in `db/schema/` and
`jobmarket_agent/extraction/`, not here. Status: [docs/ROADMAP.md](docs/ROADMAP.md).

## Tech stack

| Concern | Choice |
|---|---|
| Language | Python 3.13 |
| Agent + extraction LLM | DeepSeek |
| Structured outputs | Pydantic v2 |
| Embeddings | `bge-m3` via local Ollama, `vector(1024)` — multilingual |
| HTML parsing | BeautifulSoup 4 — Habr description bodies |
| Database | Postgres 17 + pgvector |
| API | FastAPI, SSE streaming |
| Frontend | React + Vite + Tailwind — single-page chat UI, hand-written SSE client |
| MCP | FastMCP stdio — same three tools, no DeepSeek key |
| Tracing | Langfuse (cloud — us.cloud.langfuse.com) |
| Orchestration | Docker Compose |
| Testing / evals | pytest + custom eval scripts + gold-set JSON |

## Setup

Developed on **WSL2 Ubuntu** with Docker Engine in WSL (systemd), not Docker Desktop. Requirements:
Python 3.13 (`.python-version`), [uv](https://docs.astral.sh/uv/), Docker Engine + Compose,
`postgresql-client`, a DeepSeek API key. Ollama for embeddings.

```bash
uv sync
cp .env.example .env          # fill in; .env is git-ignored
set -a; . ./.env; set +a      # host-side expansion — see below

docker compose up -d          # ./init is already in the repo; do not regenerate it
docker compose ps             # wait for healthy

for f in db/schema/*.sql; do
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"
done
```

`init/001_extensions.sql` creates `vector` and `pg_trgm` on first boot only. Overwriting it drops
`pg_trgm`. `004` fails if Adzuna rows are still present; a fresh database has none.
`POSTGRES_PASSWORD` and the password in `DATABASE_URL` must match. `docker compose` reads `.env`
itself; a bare `psql "$DATABASE_URL"` does not — source `.env` first, or the variable expands empty
on the host.

```bash
uv run python -m jobmarket_agent.ingestion.load
uv run python -m jobmarket_agent.extraction.pipeline
uv run python -m jobmarket_agent.storage.embed              # needs Ollama at OLLAMA_BASE_URL
uv run python -m jobmarket_agent.agent.loop -q "what skills show up most for senior backend roles?"
uv run uvicorn jobmarket_agent.serving.app:app
uv run python -m jobmarket_agent.mcp.server             # stdio; silence means it is waiting
```

The first Habr ingest is slow (one HTML request per posting). Later runs fetch only new ids.

Retention is keep-everything — nothing is purged. Re-snapshot before any schema change or
re-extraction you care about (snapshots are the eval baseline), to a new date-stamped file, never
in place. `evals/snapshots/2026-08-12_raw.dump` is the current one.

```bash
docker exec job-market-agent-db-1 pg_dump -U jobmarket -d jobmarket \
  -Fc -Z9 -t raw_postings > evals/snapshots/$(date +%F)_raw.dump
```

Evals use that dump restored into `jobmarket_eval`, reached as `jobmarket_ro` via
`EVAL_DATABASE_URL`. Rebuild the gold-set candidate pool with
`uv run python -m evals.generate_gold_dataset` (output is git-ignored).

Toolchain gate, before any commit:

```bash
uv run pre-commit run --all-files
uv run ruff check jobmarket_agent/ evals/ tests/ && uv run mypy jobmarket_agent/ tests/ && uv run pytest -q
```

Install the git hook once: `uv run pre-commit install`. The project is installable (`[build-system]`
in `pyproject.toml`), so `uv run` installs it editable and `import jobmarket_agent` resolves;
`pytest` keeps `pythonpath = ["."]` for the non-package `evals/` and `tests/` modules.

If `docker compose up` cannot bind 5432, a Windows Postgres service owns the port
(`Get-Service *postgres*` in an elevated PowerShell). If Ollama is on the Windows host, reach it at
`localhost:11434` and keep globs out of `no_proxy`.

## MCP

Any MCP client (Cursor, VS Code, or another agent runtime) can call the same three
tools directly. The process does not use DeepSeek — the host is the model. `vector_search` still
embeds through Ollama, so `OLLAMA_BASE_URL` must be reachable.

Do not rely on `.env`. The host's working directory is not the repo, so `load_dotenv()` will
not find it. Put `DATABASE_URL` and `OLLAMA_BASE_URL` in the config's `env` block (values from
`.env`; no `DEEPSEEK_API_KEY`). Paths must be absolute. Restart the host after editing its config.

Each host has its own config location — `.cursor/mcp.json` or `.vscode/mcp.json` in the repo,
a desktop app's `mcpServers` block. The entry shape is the same everywhere: a `command`, `args`,
and an `env` block, under a key the host reads.

```json
{
  "mcpServers": {
    "job-market": {
      "command": "/absolute/path/to/uv",
      "args": ["run", "--directory", "/absolute/path/to/repo", "python", "-m", "jobmarket_agent.mcp.server"],
      "env": {
        "DATABASE_URL": "postgresql://jobmarket:…@localhost:5432/jobmarket",
        "OLLAMA_BASE_URL": "http://localhost:11434"
      }
    }
  }
}
```

Run that `uv` command yourself before blaming the host. A process that sits silent is correct:
stdio is waiting for the host to speak first on stdin.

## Project structure

```
pyproject.toml         # deps (uv) + ruff/mypy/pytest; uv.lock is committed
.python-version        # 3.13
.env.example           # key names only; .env is git-ignored
docker-compose.yml     # Postgres 17 + pgvector; ./init mounted for first boot
init/                  # first-boot only — vector + pg_trgm
db/schema/             # re-runnable DDL, 001–010
jobmarket_agent/
  ingestion/           # four board clients, load, purge
  extraction/          # schema, transform, pipeline
  storage/             # embed, vector search, analytics
  agent/               # DeepSeek tool-calling loop
  mcp/                 # FastMCP stdio server, same three tools
  serving/             # FastAPI + SSE
frontend/             # React + Vite + Tailwind SPA (builds to dist/, served by FastAPI)
tests/
evals/                 # gold-set generator, grounding audit, frozen snapshots
docs/ROADMAP.md
```

`init/` runs once, when the data directory is empty. `db/schema/` is the re-runnable path, applied
by hand. No file belongs in both.
