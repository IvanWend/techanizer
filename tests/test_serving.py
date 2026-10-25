import asyncio
import json
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi.testclient import TestClient
from pydantic_ai import Agent
from pydantic_ai.exceptions import UsageLimitExceeded

from jobmarket_agent.agent.locale import Lang
from jobmarket_agent.agent.schema import Answer, Payload, Prose, Quote, Table, TableSpec
from jobmarket_agent.serving.api_key import ApiKey
from jobmarket_agent.serving.app import app, create_app
from jobmarket_agent.serving.sse import sse_event, stream_answer

# Payload values come from the corpus, not invention: gold_40_candidates.json
# row 1 (hn 48747990, "We The Flywheel") is the posting DECISIONS: schema cites
# for the Agentic Engineer role and its claude code / cursor / aider stack.
_PAYLOAD = Payload(
    tables=[TableSpec(kind="bars", source="skill_frequency", rows=[["claude code", 30]])],
    quotes=[
        Quote(
            id=1,
            company="We The Flywheel",
            title="Agentic Engineer",
            source="hn",
            text="ship real software with AI coding agents",
        )
    ],
    facts=[],
)

_PROSE = Prose(
    headline="Agentic Engineer roles at We The Flywheel lean on claude code.",
    key_finding="The strongest signal is claude code [1].",
)

# The frame stream_answer ships after stage 2 + assemble: prose filled (nothing
# to fill here — no facts), labels attached from the source.
_EXPECTED_ANSWER = Answer(
    headline="Agentic Engineer roles at We The Flywheel lean on claude code.",
    key_finding="The strongest signal is claude code [1].",
    tables=[Table(kind="bars", title="Most-common skills", headers=[], rows=[["claude code", 30]])],
    quotes=[
        Quote(
            id=1,
            company="We The Flywheel",
            title="Agentic Engineer",
            source="hn",
            text="ship real software with AI coding agents",
        )
    ],
)


@pytest.mark.parametrize(
    ("event", "data", "expected"),
    [
        ("answer", {"headline": "hi"}, 'event: answer\ndata: {"headline": "hi"}\n\n'),
        ("error", {"message": "boom"}, 'event: error\ndata: {"message": "boom"}\n\n'),
    ],
)
def test_sse_event_format(event: str, data: object, expected: str) -> None:
    # json.dumps, not str(): a Python-repr payload (single quotes) is not JSON,
    # and nothing on the browser side of the SSE connection could parse it.
    assert sse_event(event, data) == expected


class _StubProseAgent:
    # stream_answer calls run_prose -> prose_agent.run(json, model_settings=...).
    def __init__(self, prose: Prose | None = None) -> None:
        self._prose = prose if prose is not None else _PROSE

    async def run(
        self,
        _prompt: str,
        *,
        model_settings: object | None = None,
        usage_limits: object | None = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(output=self._prose)


class _RaisingProseAgent:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def run(self, *_args: object, **_kwargs: object) -> SimpleNamespace:
        raise self._exc


def _collect(prose_agent: object, question: str = "q", lang: Lang = "en") -> list[str]:
    # stream_answer is async but the repo has no pytest-asyncio; drain it with
    # asyncio.run inside a sync test instead.
    async def run() -> list[str]:
        p = cast(Agent[None, Prose], prose_agent)
        return [frame async for frame in stream_answer(_PAYLOAD, p, question, lang)]

    return asyncio.run(run())


def _frame_data(frame: str) -> object:
    # A frame is "event: X\ndata: <json>\n\n"; pull the data line back into a dict.
    assert frame.startswith("event: ") and "\ndata: " in frame
    return json.loads(frame.split("data: ", 1)[1].rstrip("\n"))


def test_stream_answer_maps_payload_to_answer_frame() -> None:
    # No tool-calling stage: the payload is already built, so the only frame is
    # the assembled contract (prose filled, labels attached from the source).
    frames = _collect(_StubProseAgent())
    assert len(frames) == 1
    assert _frame_data(frames[0]) == _EXPECTED_ANSWER.model_dump(mode="json")


def test_stream_answer_emits_error_frame() -> None:
    assert _collect(_RaisingProseAgent(RuntimeError("boom"))) == [
        'event: error\ndata: {"message": "boom"}\n\n'
    ]


def test_stream_answer_hides_usage_limit_url() -> None:
    # The exception appends a pydantic docs URL. The frame the UI renders must
    # not carry it — the card already has a short hint.
    exc = UsageLimitExceeded("The next tool call(s) would exceed the tool_calls_limit of 15")
    frames = _collect(_RaisingProseAgent(exc))
    assert frames == ['event: error\ndata: {"message": "tool call limit exceeded"}\n\n']
    assert "pydantic.dev" not in frames[0]


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ask_requires_query() -> None:
    # POST without a query 422s in validation, before the handler opens a DB
    # connection or builds the model, so the suite stays offline. A keyless app
    # keeps the assertion independent of any local ASK_API_KEY.
    with TestClient(create_app(api_key=ApiKey(None))) as client:
        assert client.post("/ask", json={}).status_code == 422


def test_ask_rejects_invalid_query() -> None:
    # query is a Literal: an out-of-set name 422s, not a DB lookup.
    with TestClient(create_app(api_key=ApiKey(None))) as client:
        assert client.post("/ask", json={"query": "bogus"}).status_code == 422


def test_ask_rejects_invalid_params() -> None:
    # similar_roles requires a phrase; the per-query params validator 422s before
    # any DB work (same order as the key gate).
    with TestClient(create_app(api_key=ApiKey(None))) as client:
        assert client.post("/ask", json={"query": "similar_roles", "params": {}}).status_code == 422


def test_ask_requires_key() -> None:
    with TestClient(create_app(api_key=ApiKey("secret"))) as client:
        # Missing and wrong keys 401 before the handler runs (offline-safe).
        assert client.post("/ask", json={"query": "skills"}).status_code == 401
        wrong = {"Authorization": "Bearer wrong"}
        assert client.post("/ask", json={"query": "skills"}, headers=wrong).status_code == 401
        # A valid key passes auth and reaches validation — still 422, no DB.
        ok = {"Authorization": "Bearer secret"}
        assert client.post("/ask", json={}, headers=ok).status_code == 422


def test_ask_rejects_invalid_lang() -> None:
    # lang is a Literal; an out-of-set value 422s in validation, before the
    # handler opens a DB connection or builds a model.
    with TestClient(create_app(api_key=ApiKey(None))) as client:
        assert client.post("/ask", json={"query": "skills", "lang": "xx"}).status_code == 422


def test_frontend_mounted_only_when_dist_exists(tmp_path, monkeypatch) -> None:
    # FRONTEND_DIST is read at create_app() time, so a fresh app per case keeps
    # the mount decision isolated from the module-level `app` import.
    monkeypatch.setattr("jobmarket_agent.serving.app.FRONTEND_DIST", tmp_path / "missing")
    with TestClient(create_app()) as client:
        assert client.get("/").status_code == 404
        assert client.get("/health").status_code == 200

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>jobs</title>")
    monkeypatch.setattr("jobmarket_agent.serving.app.FRONTEND_DIST", dist)
    with TestClient(create_app()) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert response.text == "<!doctype html><title>jobs</title>"
