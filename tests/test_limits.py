from pydantic_ai import UsageLimits

from techanizer.agent.limits import model_settings, usage_limits


def test_usage_limits_defaults() -> None:
    limits = usage_limits()
    assert limits.request_limit == 10
    assert limits.tool_calls_limit == 6
    assert limits.total_tokens_limit == 300_000
    assert limits.output_tokens_limit == 10_000


def test_usage_limits_env_override(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MAX_REQUESTS", "3")
    monkeypatch.setenv("AGENT_MAX_TOOL_CALLS", "5")
    monkeypatch.setenv("AGENT_TOTAL_TOKENS", "1000")
    monkeypatch.setenv("AGENT_OUTPUT_TOKENS", "250")
    limits = usage_limits()
    assert limits.request_limit == 3
    assert limits.tool_calls_limit == 5
    assert limits.total_tokens_limit == 1000
    assert limits.output_tokens_limit == 250


def test_usage_limits_is_usage_limits() -> None:
    assert isinstance(usage_limits(), UsageLimits)


def test_model_settings_timeout_and_temperature() -> None:
    settings = model_settings()
    assert settings.get("timeout") == 60
    assert settings.get("temperature") == 0
