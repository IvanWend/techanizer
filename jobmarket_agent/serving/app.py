import os
from collections.abc import AsyncIterator
from pathlib import Path

import psycopg
from anyio import to_thread
from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from jobmarket_agent.agent.loop import build_agent
from jobmarket_agent.serving.api_key import ApiKey
from jobmarket_agent.serving.rate_limit import RateLimiter
from jobmarket_agent.serving.sse import sse_event, stream_answer

load_dotenv()

url = os.environ.get("DATABASE_URL")

# Served only when the frontend has been built: dist/ is gitignored and CI has
# no node build, so a fresh clone mounts nothing and /health still answers.
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


class AskRequest(BaseModel):
    question: str


def _connect() -> psycopg.Connection:
    if not url:
        raise ValueError("DATABASE_URL is not set")
    return psycopg.connect(url)


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


async def _stream(question: str) -> AsyncIterator[str]:
    conn: psycopg.Connection | None = None
    try:
        conn = await to_thread.run_sync(_connect)
        agent = build_agent(conn=conn)
        async for frame in stream_answer(agent, question):
            yield frame
    except Exception as e:
        yield sse_event("error", {"message": str(e)})
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

    @app.get("/ask", dependencies=[Depends(rate_limiter), Depends(api_key)])
    def ask_get(q: str):
        return StreamingResponse(_stream(q), media_type="text/event-stream")

    @app.post("/ask", dependencies=[Depends(rate_limiter), Depends(api_key)])
    def ask_post(request: AskRequest):
        return StreamingResponse(_stream(request.question), media_type="text/event-stream")

    # Mounted last so /health, /ask and the docs routes win; StaticFiles(html=True)
    # serves index.html at "/" for the single-page chat UI. Absent dist/ -> no mount.
    if FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")

    return app


app = create_app()
