import argparse
import asyncio
import json
import os

import psycopg
from dotenv import load_dotenv
from pydantic_ai import Agent, AgentRunResult
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tool_manager import ToolManager

from techanizer.agent.limits import model_settings, usage_limits
from techanizer.agent.locale import REFUSAL, TABLE_LABELS, Lang, fill_placeholders
from techanizer.agent.prompt import SYSTEM_PROMPT, prose_prompt
from techanizer.agent.schema import Answer, Payload, Prose, Quote, Table, TableSpec
from techanizer.agent.tools import make_role_detail, make_sql_query, make_vector_search
from techanizer.models import resolve_model
from techanizer.storage import role

load_dotenv()

DEFAULT_STAGE1_MODEL = "alibaba:glm-5.3"
DEFAULT_STAGE2_MODEL = "alibaba:qwen3.8-flash"
DEFAULT_STAGE2_RU_MODEL = "alibaba:qwen3.8-flash"


def build_agent(
    model: str = DEFAULT_STAGE1_MODEL, conn=None, system_prompt: str = SYSTEM_PROMPT
) -> Agent[None, Payload]:
    tools = [make_sql_query(conn), make_vector_search(conn), make_role_detail(conn)]
    return Agent(
        model=resolve_model(model),
        tools=tools,
        system_prompt=system_prompt,
        output_type=Payload,
        retries=3,
        defer_model_check=True,
    )


def prose_model_settings(model: str) -> ModelSettings:
    # All current prose models (Alibaba Qwen flash) accept temperature=0; `model`
    # stays as the hook for a future locale whose model rejects it (Kimi did).
    return model_settings()


def default_stage2_model(lang: Lang) -> str:
    return DEFAULT_STAGE2_RU_MODEL if lang == "ru" else DEFAULT_STAGE2_MODEL


def build_prose_agent(lang: Lang = "en", model: str | None = None) -> Agent[None, Prose]:
    # No tools: stage 2 only writes prose from the stage-1 payload. The payload is
    # passed as a JSON string prompt, matching how the judge runs (no typed deps).
    return Agent(
        model=resolve_model(model or default_stage2_model(lang)),
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


async def run_prose(payload: Payload, prose_agent: Agent[None, Prose], model: str) -> Prose:
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


def refusal_answer(lang: Lang) -> Answer:
    # Code-owned and localized: a refusal has no prose to write and no data to
    # render, so the headline is the whole answer. `refused` marks it for the
    # frontend and the eval, which grade refusal as the correct outcome.
    return Answer(refused=True, headline=REFUSAL[lang], key_finding="", tables=[], quotes=[])


def assemble(payload: Payload, prose: Prose, lang: Lang) -> Answer:
    if payload.refused:
        return refusal_answer(lang)
    return Answer(
        headline=fill_placeholders(prose.headline, payload.facts, lang),
        key_finding=fill_placeholders(prose.key_finding, payload.facts, lang),
        tables=[_table(ts, lang) for ts in payload.tables],
        quotes=payload.quotes,
    )


def fill_quote_details(conn, quotes: list[Quote]) -> None:
    """Attach each quote's original posting URL and description in code, resolved
    from the (source, company, title) triple the model copies verbatim — Qwen
    drops optional url/description fields it is told to copy, so the link rides
    the fields the quote already carries. Runs after stage 2 (the prose prompt
    never sees the descriptions) and before assemble; a miss leaves the quote
    as-is — a missing link, never a failed answer."""
    for quote in quotes:
        try:
            link = role.quote_link(conn, quote.source, quote.company, quote.title, quote.text)
        except psycopg.Error:
            # A link lookup must never fail the answer — the tools and prose
            # already succeeded; ship the quote without its link.
            continue
        if link is None:
            continue
        quote.url = link.url
        quote.description = link.description


async def compose(
    payload: Payload,
    lang: Lang,
    prose_agent: Agent[None, Prose],
    model: str | None = None,
    conn=None,
) -> Answer:
    if payload.refused:
        # No stage-2 prose call: the refusal message is code-owned, so a refused
        # question must not spend a prose request (or leak unlocalized text).
        return refusal_answer(lang)
    resolved = model or default_stage2_model(lang)
    prose = await run_prose(payload, prose_agent, resolved)
    if conn is not None:
        fill_quote_details(conn, payload.quotes)
    return assemble(payload, prose, lang)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Techanizer tech job-market analysis agent")
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
        default=None,
        help="Override the stage-2 prose model (defaults per locale: RU Kimi, EN DeepSeek).",
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
        out = await compose(result.output, args.lang, prose_agent, args.prose_model, conn=conn)
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
