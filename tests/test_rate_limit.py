from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from jobmarket_agent.serving.app import create_app
from jobmarket_agent.serving.rate_limit import RateLimiter


def _req(host: str) -> Any:
    # The limiter reads only request.client.host; a namespace stands in for the
    # full Request so the tests run without a live ASGI request.
    return cast(Any, SimpleNamespace(client=SimpleNamespace(host=host)))


def test_under_limit_passes() -> None:
    limiter = RateLimiter(max_requests=2, window_seconds=60, now=lambda: 1000.0)
    limiter(_req("1.1.1.1"))
    limiter(_req("1.1.1.1"))


def test_over_limit_raises_429_with_retry_after() -> None:
    limiter = RateLimiter(max_requests=1, window_seconds=60, now=lambda: 1000.0)
    limiter(_req("1.1.1.1"))
    with pytest.raises(HTTPException) as exc:
        limiter(_req("1.1.1.1"))
    assert exc.value.status_code == 429
    headers = exc.value.headers
    assert headers is not None
    assert int(headers["Retry-After"]) >= 1


def test_window_resets_after_window_elapses() -> None:
    clock = [1000.0]
    limiter = RateLimiter(max_requests=1, window_seconds=60, now=lambda: clock[0])
    limiter(_req("1.1.1.1"))
    with pytest.raises(HTTPException):
        limiter(_req("1.1.1.1"))
    clock[0] = 1060.0  # window expired
    limiter(_req("1.1.1.1"))


def test_per_ip_isolation() -> None:
    limiter = RateLimiter(max_requests=1, window_seconds=60, now=lambda: 1000.0)
    limiter(_req("1.1.1.1"))
    with pytest.raises(HTTPException):
        limiter(_req("1.1.1.1"))
    limiter(_req("2.2.2.2"))


def test_ask_rate_limited_returns_429() -> None:
    # End-to-end: the dependency is wired to /ask. The first request streams an
    # error frame (no DB in CI) but still 200s; the second trips the cap.
    limiter = RateLimiter(max_requests=1, window_seconds=60)
    with TestClient(create_app(rate_limiter=limiter)) as client:
        first = client.get("/ask", params={"q": "hello"})
        second = client.get("/ask", params={"q": "hello"})
    assert first.status_code == 200
    assert second.status_code == 429
    assert "Retry-After" in second.headers
