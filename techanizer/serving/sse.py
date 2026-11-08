import json
from collections.abc import AsyncIterator

from pydantic_ai import (
    Agent,
    AgentRunResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
)
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.tool_manager import ToolManager

from techanizer.agent.limits import model_settings, usage_limits
from techanizer.agent.locale import Lang
from techanizer.agent.loop import (
    assemble,
    default_stage2_model,
    fill_quote_details,
    refusal_answer,
    run_prose,
)
from techanizer.agent.schema import Payload, Prose


def sse_event(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def error_message(exc: BaseException) -> str:
    # UsageLimitExceeded appends a pydantic docs URL. The UI already shows a
    # short hint; the frame must not ship the URL or the raw limit dump.
    if isinstance(exc, UsageLimitExceeded):
        return "tool call limit exceeded"
    return str(exc)


def summarize_tool_result(name: str, content: object) -> dict[str, object]:
    # A per-call result summary, shipped so the UI can show what each tool call
    # produced, not just its name. Structured (not prose) so the browser localizes
    # the count and the company stays a proper noun. `content` is the tool's raw
    # return value: a list of dicts for sql_query/vector_search, a dict (or None)
    # for role_detail.
    if name == "role_detail":
        if isinstance(content, dict):
            parts = [p for p in (content.get("company"), content.get("title")) if p]
            return {"kind": "company", "company": " · ".join(parts) or "posting"}
        return {"kind": "none"}
    if isinstance(content, list):
        return {"kind": "count", "n": len(content)}
    return {"kind": "none"}


async def stream_answer(
    stage1_agent: Agent[None, Payload],
    prose_agent: Agent[None, Prose],
    question: str,
    lang: Lang,
    conn=None,
) -> AsyncIterator[str]:
    with ToolManager.parallel_execution_mode("sequential"):
        async with stage1_agent:
            try:
                async with stage1_agent.run_stream_events(
                    question,
                    model_settings=model_settings(),
                    usage_limits=usage_limits(),
                ) as events:
                    async for ev in events:
                        if isinstance(ev, FunctionToolCallEvent):
                            # Name only: the UI maps it to a one-line status
                            # phrase — the tool's output is never shipped.
                            yield sse_event("tool_call", {"name": ev.part.tool_name})
                        elif isinstance(ev, FunctionToolResultEvent) and isinstance(
                            ev.part, ToolReturnPart
                        ):
                            # The result summary lets the UI show "what just
                            # happened" next to each tool call, not just its name.
                            yield sse_event(
                                "tool_result",
                                {
                                    "name": ev.part.tool_name,
                                    "summary": summarize_tool_result(
                                        ev.part.tool_name, ev.part.content
                                    ),
                                },
                            )
                        elif isinstance(ev, AgentRunResultEvent):
                            payload = ev.result.output
                            if payload is None:
                                yield sse_event("error", {"message": "no output"})
                                return
                            if payload.refused:
                                # No stage-2 prose for a refusal: the message is
                                # code-owned and localized, nothing to compose.
                                yield sse_event(
                                    "answer", refusal_answer(lang).model_dump(mode="json")
                                )
                                return
                            # Stage 2 is a silent multi-second call otherwise —
                            # tell the UI the tools are done and prose is writing.
                            yield sse_event("status", {"phase": "prose"})
                            prose = await run_prose(
                                payload, prose_agent, default_stage2_model(lang)
                            )
                            # Attach posting links after stage 2: the prose
                            # prompt never sees the descriptions, and the link
                            # cannot be dropped by the model (see loop.py).
                            if conn is not None:
                                fill_quote_details(conn, payload.quotes)
                            answer = assemble(payload, prose, lang)
                            yield sse_event("answer", answer.model_dump(mode="json"))
            except Exception as e:
                yield sse_event("error", {"message": error_message(e)})
                return
