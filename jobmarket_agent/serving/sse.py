import json
from collections.abc import AsyncIterator

from pydantic_ai import (
    Agent,
    AgentRunResultEvent,
    FunctionToolCallEvent,
)
from pydantic_ai.tool_manager import ToolManager

from jobmarket_agent.agent.limits import model_settings, usage_limits
from jobmarket_agent.agent.locale import Lang
from jobmarket_agent.agent.loop import assemble, default_stage2_model, run_prose
from jobmarket_agent.agent.schema import Payload, Prose


def sse_event(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


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
                            # Name only: the UI shows a one-line status; the tool's
                            # output is never shipped — the contract carries what the
                            # answer actually depends on.
                            yield sse_event("tool_call", {"name": ev.part.tool_name})
                        elif isinstance(ev, AgentRunResultEvent):
                            payload = ev.result.output
                            if payload is None:
                                yield sse_event("error", {"message": "no output"})
                                return
                            prose = await run_prose(
                                payload, prose_agent, default_stage2_model(lang)
                            )
                            answer = assemble(payload, prose, lang)
                            yield sse_event("answer", answer.model_dump(mode="json"))
            except Exception as e:
                yield sse_event("error", {"message": str(e)})
                return
