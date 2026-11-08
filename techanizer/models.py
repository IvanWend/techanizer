import os

from openai import AsyncOpenAI
from pydantic_ai.models import Model, infer_model
from pydantic_ai.providers.alibaba import AlibabaProvider

# Alibaba's compatible-mode endpoint for China-region keys. pydantic-ai's
# built-in AlibabaProvider defaults to the *intl* endpoint
# (dashscope-intl.aliyuncs.com), which 401s on these keys, so every `alibaba:*`
# model must resolve here (see DECISIONS: Agent "2026-11-08").
ALIBABA_CN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def resolve_model(model: str) -> str | Model:
    """Map a provider-qualified model string to a concrete Model when the provider
    needs a non-default endpoint; otherwise return the string so pydantic-ai infers
    it lazily at first run (which keeps offline builds and CI key-free)."""
    if model.startswith("alibaba:"):
        client = AsyncOpenAI(
            base_url=ALIBABA_CN_BASE_URL,
            api_key=os.environ.get("ALIBABA_API_KEY") or "offline-placeholder",
        )
        alibaba = AlibabaProvider(openai_client=client)
        return infer_model(model, provider_factory=lambda _name: alibaba)
    return model
