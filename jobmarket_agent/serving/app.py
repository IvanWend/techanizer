import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import psycopg
from anyio import to_thread
from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, model_validator

from jobmarket_agent.agent.locale import Lang
from jobmarket_agent.agent.loop import build_prose_agent
from jobmarket_agent.agent.menu import QueryName, build_payload, question_for, validate_params
from jobmarket_agent.serving.api_key import ApiKey
from jobmarket_agent.serving.rate_limit import RateLimiter
from jobmarket_agent.serving.sse import error_message, sse_event, stream_answer

load_dotenv()

url = os.environ.get("DATABASE_URL")

# Served only when the frontend has been built: dist/ is gitignored and CI has
# no node build, so a fresh clone mounts nothing and /health still answers.
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


class MenuRequest(BaseModel):
    # The wire contract: a fixed query shape + typed params, not a free question.
    # params is validated against the query's schema here so a bad request 422s
    # before the handler opens a DB connection (same order as the key gate).
    query: QueryName
    params: dict[str, Any] = {}
    lang: Lang = "en"

    @model_validator(mode="after")
    def _params_valid(self) -> "MenuRequest":
        validate_params(self.query, self.params)
        return self


def _connect() -> psycopg.Connection:
    if not url:
        raise ValueError("DATABASE_URL is not set")
    return psycopg.connect(url)


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


async def _stream(query: QueryName, params: dict[str, Any], lang: Lang) -> AsyncIterator[str]:
    conn: psycopg.Connection | None = None
    try:
        conn = await to_thread.run_sync(_connect)
        # build_payload runs sync (DB + embedding) — off the loop via to_thread.
        payload = await to_thread.run_sync(build_payload, query, params, conn)
        question = question_for(query, params, lang)
        prose_agent = build_prose_agent(lang=lang)
        async for frame in stream_answer(payload, prose_agent, question, lang):
            yield frame
    except Exception as e:
        yield sse_event("error", {"message": error_message(e)})
    finally:
        if conn is not None:
            await to_thread.run_sync(conn.close)


def create_app(
    rate_limiter: RateLimiter | None = None,
    api_key: ApiKey | None = None,
) -> FastAPI:
    if rate_limiter is None:
        rate_limiter = RateLimiter(
            max_requests=_int("RATE_LIMIT_MAX", 30),
            window_seconds=_int("RATE_LIMIT_WINDOW_SECONDS", 60),
        )
    # Unset ASK_API_KEY keeps /ask open (dev + CI); set it to gate the endpoint.
    if api_key is None:
        api_key = ApiKey(os.environ.get("ASK_API_KEY") or None)
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/ask", dependencies=[Depends(rate_limiter), Depends(api_key)])
    def ask_post(request: MenuRequest):
        return StreamingResponse(
            _stream(request.query, request.params, request.lang), media_type="text/event-stream"
        )

    # Mounted last so /health, /ask and the docs routes win; StaticFiles(html=True)
    # serves index.html at "/" for the single-page chat UI. Absent dist/ -> no mount.
    if FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")

    return app


app = create_app()
