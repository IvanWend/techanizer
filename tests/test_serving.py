import asyncio
import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from pydantic_ai import (
    Agent,
    AgentRunResultEvent,
    FunctionToolCallEvent,
)
from pydantic_ai.exceptions import UsageLimitExceeded

from techanizer.agent.locale import Lang
from techanizer.agent.schema import Answer, Payload, Prose, Quote, Table, TableSpec
from techanizer.serving.api_key import ApiKey
from techanizer.serving.app import app, create_app
from techanizer.serving.sse import sse_event, stream_answer

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


_TOOL_CALL_FRAME = 'event: tool_call\ndata: {"name": "vector_search"}\n\n'


@pytest.mark.parametrize(
    ("event", "data", "expected"),
    [
        ("tool_call", {"name": "vector_search"}, _TOOL_CALL_FRAME),
        ("error", {"message": "boom"}, 'event: error\ndata: {"message": "boom"}\n\n'),
    ],
)
def test_sse_event_format(event: str, data: object, expected: str) -> None:
    # json.dumps, not str(): a Python-repr payload (single quotes) is not JSON,
    # and nothing on the browser side of the SSE connection could parse it.
    assert sse_event(event, data) == expected


class _StubEvents:
    # run_stream_events' contract: an async CM handing out the event iterator.
    def __init__(self, events: list[object], exc: Exception | None = None) -> None:
        self._events = events
        self._exc = exc

    async def __aenter__(self) -> AsyncIterator[object]:
        return self._agen()

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def _agen(self) -> AsyncIterator[object]:
        for ev in self._events:
            yield ev
        if self._exc is not None:
            raise self._exc


class _StubStage1Agent:
    # Only the Agent surface stream_answer touches: async CM + run_stream_events.
    def __init__(self, events: list[object], exc: Exception | None = None) -> None:
        self._events = _StubEvents(events, exc)

    async def __aenter__(self) -> "_StubStage1Agent":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def run_stream_events(
        self,
        _question: str,
        *,
        model_settings: object | None = None,
        usage_limits: object | None = None,
    ) -> _StubEvents:
        return self._events


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


def _collect(
    stage1_agent: _StubStage1Agent, prose_agent: _StubProseAgent, question: str, lang: Lang = "en"
) -> list[str]:
    # stream_answer is async but the repo has no pytest-asyncio; drain it with
    # asyncio.run inside a sync test instead.
    async def run() -> list[str]:
        s1 = cast(Agent[None, Payload], stage1_agent)
        p = cast(Agent[None, Prose], prose_agent)
        return [frame async for frame in stream_answer(s1, p, question, lang)]

    return asyncio.run(run())


def _frame_data(frame: str) -> object:
    # A frame is "event: X\ndata: <json>\n\n"; pull the data line back into a dict.
    assert frame.startswith("event: ") and "\ndata: " in frame
    return json.loads(frame.split("data: ", 1)[1].rstrip("\n"))


def test_stream_answer_maps_events_to_frames() -> None:
    events = [
        FunctionToolCallEvent(
            part=cast(Any, SimpleNamespace(tool_name="vector_search")),
            args_valid=True,
        ),
        AgentRunResultEvent(result=cast(Any, SimpleNamespace(output=_PAYLOAD))),
    ]

    frames = _collect(_StubStage1Agent(events), _StubProseAgent(), "agentic engineer")
    assert len(frames) == 3
    # tool_call is name-only — the UI maps it to a one-line status phrase.
    assert frames[0] == 'event: tool_call\ndata: {"name": "vector_search"}\n\n'
    # The status frame marks the stage-2 prose call, which is silent otherwise.
    assert frames[1] == 'event: status\ndata: {"phase": "prose"}\n\n'
    # The answer frame is the assembled contract (prose filled, labels attached).
    assert _frame_data(frames[2]) == _EXPECTED_ANSWER.model_dump(mode="json")


def test_stream_answer_emits_error_frame() -> None:
    stub = _StubStage1Agent([], exc=RuntimeError("boom"))
    assert _collect(stub, _StubProseAgent(), "q") == ['event: error\ndata: {"message": "boom"}\n\n']


def test_stream_answer_emits_refusal_without_prose() -> None:
    # A refused payload skips stage 2: the single answer frame carries the flag
    # and the localized refusal message, with no tables/quotes and no tool_call.
    events: list[object] = [
        AgentRunResultEvent(result=cast(Any, SimpleNamespace(output=Payload(refused=True)))),
    ]
    frames = _collect(_StubStage1Agent(events), _StubProseAgent(), "years of experience?")
    assert len(frames) == 1
    data = cast(dict, _frame_data(frames[0]))
    assert data["refused"] is True
    assert data["tables"] == [] and data["quotes"] == []
    assert data["headline"]


def test_stream_answer_hides_usage_limit_url() -> None:
    # The exception appends a pydantic docs URL. The frame the UI renders must
    # not carry it — the card already has a short hint.
    exc = UsageLimitExceeded("The next tool call(s) would exceed the tool_calls_limit of 15")
    frames = _collect(_StubStage1Agent([], exc=exc), _StubProseAgent(), "q")
    assert frames == ['event: error\ndata: {"message": "tool call limit exceeded"}\n\n']
    assert "pydantic.dev" not in frames[0]


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ask_requires_query() -> None:
    # Both requests 422 in validation, before the handler opens a DB
    # connection or builds the model, so the suite stays offline. A keyless app
    # keeps the assertion independent of any local ASK_API_KEY.
    with TestClient(create_app(api_key=ApiKey(None))) as client:
        assert client.get("/ask").status_code == 422
        assert client.post("/ask", json={}).status_code == 422


def test_ask_requires_key() -> None:
    with TestClient(create_app(api_key=ApiKey("secret"))) as client:
        # Missing and wrong keys 401 before the handler runs (offline-safe).
        assert client.post("/ask", json={"question": "hi"}).status_code == 401
        wrong = {"Authorization": "Bearer wrong"}
        assert client.post("/ask", json={"question": "hi"}, headers=wrong).status_code == 401
        # A valid key passes auth and reaches validation — still 422, no DB.
        ok = {"Authorization": "Bearer secret"}
        assert client.post("/ask", json={}, headers=ok).status_code == 422
        assert client.get("/ask", headers=ok).status_code == 422


def test_ask_rejects_invalid_lang() -> None:
    # lang is a Literal; an out-of-set value 422s in validation, before the
    # handler opens a DB connection or builds a model.
    with TestClient(create_app(api_key=ApiKey(None))) as client:
        assert client.get("/ask", params={"q": "hi", "lang": "xx"}).status_code == 422
        assert client.post("/ask", json={"question": "hi", "lang": "xx"}).status_code == 422


def test_frontend_mounted_only_when_dist_exists(tmp_path, monkeypatch) -> None:
    # FRONTEND_DIST is read at create_app() time, so a fresh app per case keeps
    # the mount decision isolated from the module-level `app` import.
    monkeypatch.setattr("techanizer.serving.app.FRONTEND_DIST", tmp_path / "missing")
    with TestClient(create_app()) as client:
        assert client.get("/").status_code == 404
        assert client.get("/health").status_code == 200

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>jobs</title>")
    monkeypatch.setattr("techanizer.serving.app.FRONTEND_DIST", dist)
    with TestClient(create_app()) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert response.text == "<!doctype html><title>jobs</title>"
