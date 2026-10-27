import json
from collections.abc import AsyncIterator

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
                            # Name only: the UI shows a step in a checklist; the
                            # tool's output is never shipped — the contract
                            # carries what the answer actually depends on.
                            yield sse_event("tool_call", {"name": ev.part.tool_name})
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
