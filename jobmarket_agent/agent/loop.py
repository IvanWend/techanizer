import argparse
import asyncio
import json
import os

import psycopg
from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic_ai import Agent, AgentRunResult
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.models import Model, infer_model
from pydantic_ai.providers.moonshotai import MoonshotAIProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tool_manager import ToolManager

from jobmarket_agent.agent.limits import model_settings, usage_limits
from jobmarket_agent.agent.locale import TABLE_LABELS, Lang, fill_placeholders
from jobmarket_agent.agent.prompt import SYSTEM_PROMPT, prose_prompt
from jobmarket_agent.agent.schema import Answer, Payload, Prose, Table, TableSpec
from jobmarket_agent.agent.tools import make_role_detail, make_sql_query, make_vector_search

load_dotenv()

DEFAULT_STAGE1_MODEL = "deepseek:deepseek-v4-flash"
# Stage 2 writes prose by locale; DeepSeek is the fallback (PLAN: A/B a RU model).
DEFAULT_STAGE2_MODEL = "deepseek:deepseek-v4-flash"

# The user's KIMI_API_KEY is a China key; pydantic-ai's MoonshotAI provider
# hardcodes api.moonshot.ai and 401s on it, so a moonshotai:* model is resolved
# against api.moonshot.cn instead (same as the eval judge).
KIMI_BASE_URL = "https://api.moonshot.cn/v1"


def build_agent(
    model: str = DEFAULT_STAGE1_MODEL, conn=None, system_prompt: str = SYSTEM_PROMPT
) -> Agent[None, Payload]:
    tools = [make_sql_query(conn), make_vector_search(conn), make_role_detail(conn)]
    # defer_model_check skips infer_model (and its API-key lookup) until the first
    # ask()/run. CI and the offline contract test construct an agent with no key.
    return Agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        output_type=Payload,
        defer_model_check=True,
    )


def resolve_prose_model(model: str) -> str | Model:
    # DeepSeek stays a string, inferred lazily (defer_model_check) so CI/test build
    # offline. A moonshotai:* model is resolved eagerly against api.moonshot.cn —
    # the endpoint the China key works against — with a custom client.
    if model.startswith("moonshotai:"):
        client = AsyncOpenAI(base_url=KIMI_BASE_URL, api_key=os.environ["KIMI_API_KEY"])
        provider = MoonshotAIProvider(openai_client=client)
        return infer_model(model, provider_factory=lambda _name: provider)
    return model


def prose_model_settings(model: str) -> ModelSettings:
    # kimi-k3 rejects temperature=0 (400 "only 1 is allowed"); run it on its
    # default temperature. DeepSeek keeps temperature=0 for deterministic prose.
    if model.startswith("moonshotai:"):
        return ModelSettings(timeout=120)
    return model_settings()


def build_prose_agent(lang: Lang = "en", model: str = DEFAULT_STAGE2_MODEL) -> Agent[None, Prose]:
    # No tools: stage 2 only writes prose from the stage-1 payload. The payload is
    # passed as a JSON string prompt, matching how the judge runs (no typed deps).
    return Agent(
        model=resolve_prose_model(model),
        system_prompt=prose_prompt(lang),
        output_type=Prose,
        defer_model_check=True,
    )


async def ask(agent: Agent[None, Payload], question: str) -> AgentRunResult[Payload]:
    with ToolManager.parallel_execution_mode("sequential"):
        return await agent.run(
            user_prompt=question,
            model_settings=model_settings(),
            usage_limits=usage_limits(),
        )


async def run_prose(
    payload: Payload, prose_agent: Agent[None, Prose], model: str = DEFAULT_STAGE2_MODEL
) -> Prose:
    result = await prose_agent.run(
        json.dumps(payload.model_dump(mode="json"), ensure_ascii=False),
        model_settings=prose_model_settings(model),
        usage_limits=usage_limits(),
    )
    if result.output is None:
        raise ValueError("prose model returned no output")
    return result.output


def _table(ts: TableSpec, lang: Lang) -> Table:
    title, headers = TABLE_LABELS[ts.source][lang]
    return Table(kind=ts.kind, title=title, headers=headers, rows=ts.rows)


def assemble(payload: Payload, prose: Prose, lang: Lang) -> Answer:
    return Answer(
        headline=fill_placeholders(prose.headline, payload.facts, lang),
        key_finding=fill_placeholders(prose.key_finding, payload.facts, lang),
        tables=[_table(ts, lang) for ts in payload.tables],
        quotes=payload.quotes,
    )


async def compose(
    payload: Payload,
    lang: Lang,
    prose_agent: Agent[None, Prose],
    model: str = DEFAULT_STAGE2_MODEL,
) -> Answer:
    return assemble(payload, await run_prose(payload, prose_agent, model), lang)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Pydantic AI DeepSeek Data Assistant Loop")
    parser.add_argument(
        "--question",
        "-q",
        type=str,
        required=True,
        help="The natural language question to ask the agent.",
    )
    parser.add_argument(
        "--model",
        "-m",
        type=str,
        default=DEFAULT_STAGE1_MODEL,
        help="The stage-1 (tool-calling) model string (default: flash version).",
    )
    parser.add_argument(
        "--prose-model",
        type=str,
        default=DEFAULT_STAGE2_MODEL,
        help="The stage-2 (prose) model string.",
    )
    parser.add_argument(
        "--lang",
        type=str,
        choices=["en", "ru"],
        default="en",
        help="Locale for the prose.",
    )
    args = parser.parse_args()

    db_url = os.environ.get("EVAL_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not db_url:
        raise ValueError(
            "Database connection URL missing. Please set EVAL_DATABASE_URL or DATABASE_URL."
        )

    with psycopg.connect(db_url) as conn:
        agent = build_agent(model=args.model, conn=conn)
        prose_agent = build_prose_agent(lang=args.lang, model=args.prose_model)
        result = await ask(agent, args.question)
        if result.output is None:
            raise ValueError("stage 1 returned no output")
        out = await compose(result.output, args.lang, prose_agent, args.prose_model)
        print(out.headline)
        print(out.key_finding)
        for t in out.tables:
            print(f"[{t.kind}] {t.title}")
            if t.headers:
                print("  ", " | ".join(t.headers))
            for row in t.rows:
                print("  ", " | ".join("—" if c is None else str(c) for c in row))
        for q in out.quotes:
            print(f"[{q.id}] {q.company} — {q.title} ({q.source}): {q.text!r}")

        tool_calls_count = sum(
            1
            for msg in result.all_messages()
            if hasattr(msg, "parts")
            for part in msg.parts
            if isinstance(part, ToolCallPart)
        )

        print(f"Called {tool_calls_count} tools")


if __name__ == "__main__":
    asyncio.run(main())
