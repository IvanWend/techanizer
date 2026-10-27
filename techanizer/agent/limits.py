import os

from pydantic_ai import UsageLimits
from pydantic_ai.settings import ModelSettings


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


def usage_limits() -> UsageLimits:
    # request_limit = max turns (one model request each). Token caps are the
    # spend proxy: the model registry carries no DeepSeek cost, so cost_limit
    # would warn and never fire (see DECISIONS: Agent).
    # tool_calls_limit=6 matches the prompt's own fan-out caps (1 sql_query +
    # 1 vector_search + 2 role_detail = 4) plus headroom; a higher cap only
    # lengthens a runaway loop (see DECISIONS: Agent).
    return UsageLimits(
        request_limit=_int("AGENT_MAX_REQUESTS", 10),
        tool_calls_limit=_int("AGENT_MAX_TOOL_CALLS", 6),
        total_tokens_limit=_int("AGENT_TOTAL_TOKENS", 300_000),
        output_tokens_limit=_int("AGENT_OUTPUT_TOKENS", 10_000),
    )


def model_settings() -> ModelSettings:
    return ModelSettings(timeout=60, temperature=0)
