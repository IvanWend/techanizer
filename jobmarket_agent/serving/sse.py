import json
from collections.abc import AsyncIterator
from typing import Any, cast

from pydantic_ai import (
    Agent,
    AgentRunResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
)
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.tool_manager import ToolManager

from jobmarket_agent.agent.limits import model_settings, usage_limits
from jobmarket_agent.agent.locale import Lang
from jobmarket_agent.agent.loop import assemble, default_stage2_model, refusal_answer, run_prose
from jobmarket_agent.agent.schema import Payload, Prose


def sse_event(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def error_message(exc: BaseException) -> str:
    # UsageLimitExceeded appends a pydantic docs URL. The UI already shows a
    # short hint; the frame must not ship the URL or the raw limit dump.
    if isinstance(exc, UsageLimitExceeded):
        return "tool call limit exceeded"
    return str(exc)


def _resolve_args(raw_args: str | dict[str, Any]) -> dict[str, Any]:
    # pydantic-ai 2.24 serialises args as JSON at runtime (DECISIONS: Serving),
    # but the type is str | dict — handle both.
    if isinstance(raw_args, dict):
        return raw_args
    return cast(dict[str, Any], json.loads(raw_args))


def tool_summary(tool_name: str, raw_args: str | dict[str, Any]) -> str:
    """One-line natural-language summary of what the tool was asked to do."""
    args = _resolve_args(raw_args)
    if tool_name == "vector_search":
        query = str(args.get("query", ""))
        seniority = args.get("seniority")
        remote = args.get("remote")
        parts = [f"looking for '{query}'"]
        if seniority:
            parts.append(f"seniority: {', '.join(seniority)}")
        if remote:
            parts.append(f"remote: {', '.join(remote)}")
        return " · ".join(parts)
    if tool_name == "sql_query":
        query = args.get("query", "")
        limit = args.get("limit", 20)
        currency = args.get("currency", "USD")
        if query == "skill_frequency":
            return f"most common skills (top {limit})"
        if query == "salary_by_currency":
            return "salaries by currency"
        if query == "salary_by_seniority":
            return f"salaries by level ({currency})"
        if query == "top_salaries":
            return f"highest-paid roles ({currency}, top {limit})"
        return f"sql_query: {query}"
    if tool_name == "role_detail":
        pid = args.get("raw_posting_id", "?")
        ridx = args.get("role_index", "?")
        return f"reading posting #{pid} role #{ridx}"
    return tool_name


async def stream_answer(
    stage1_agent: Agent[None, Payload],
    prose_agent: Agent[None, Prose],
    question: str,
    lang: Lang,
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
                            # Name + human-readable summary: the UI renders what
                            # the agent actually asked, not a phase label.
                            summary = tool_summary(ev.part.tool_name, ev.part.args or {})
                            yield sse_event(
                                "tool_call",
                                {"name": ev.part.tool_name, "summary": summary},
                            )
                        elif isinstance(ev, FunctionToolResultEvent):
                            # Marks the step done. Name only, same as the call.
                            yield sse_event("tool_return", {"name": ev.part.tool_name})
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
                            answer = assemble(payload, prose, lang)
                            yield sse_event("answer", answer.model_dump(mode="json"))
            except Exception as e:
                yield sse_event("error", {"message": error_message(e)})
                return
