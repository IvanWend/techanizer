import json
from collections.abc import AsyncIterator

from pydantic_ai import Agent
from pydantic_ai.exceptions import UsageLimitExceeded

from jobmarket_agent.agent.locale import Lang
from jobmarket_agent.agent.loop import assemble, default_stage2_model, run_prose
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
    payload: Payload,
    prose_agent: Agent[None, Prose],
    question: str,
    lang: Lang,
) -> AsyncIterator[str]:
    # No tool-calling stage: the payload is already built by the menu, so the
    # only model call left is prose. One answer frame (or an error frame).
    try:
        prose = await run_prose(payload, prose_agent, default_stage2_model(lang), question)
        answer = assemble(payload, prose, lang)
        yield sse_event("answer", answer.model_dump(mode="json"))
    except Exception as e:
        yield sse_event("error", {"message": error_message(e)})
