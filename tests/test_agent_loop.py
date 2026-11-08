import asyncio

from techanizer.agent.locale import REFUSAL
from techanizer.agent.loop import (
    DEFAULT_STAGE2_MODEL,
    DEFAULT_STAGE2_RU_MODEL,
    assemble,
    build_agent,
    build_prose_agent,
    compose,
    default_stage2_model,
    fill_quote_details,
    prose_model_settings,
)
from techanizer.agent.prompt import SYSTEM_PROMPT
from techanizer.agent.schema import Fact, Payload, Prose, Quote, TableSpec
from techanizer.models import resolve_model


def test_stage1_prompt_does_not_invite_tool_fanout() -> None:
    # The old "count the stack across hits" line made the model search, then
    # role_detail every hit, and die at tool_calls_limit=15. Caps are the rule.
    assert "count the `stack`" not in SYSTEM_PROMPT
    assert "Call vector_search at most once" in SYSTEM_PROMPT
    assert "Call role_detail at most twice" in SYSTEM_PROMPT
    # Leak phrases the injection grader still looks for. "never from memory" is
    # wrapped in the source, so the contiguous half is what the string holds.
    assert "canned analytics" in SYSTEM_PROMPT
    assert "from memory" in SYSTEM_PROMPT
    assert "job-market analyst" in SYSTEM_PROMPT


def test_stage1_prompt_has_refusal_rule() -> None:
    # The wrong-answer bug was a refusal failure: out-of-scope questions returned
    # the corpus-wide table. The prompt must name the refusal path explicitly.
    assert "refused=true" in SYSTEM_PROMPT
    assert "Refuse out-of-scope questions" in SYSTEM_PROMPT


def test_build_agent_registers_three_tools() -> None:
    # No DB and no model: build_agent is pure, so a bare object() would
    # AttributeError on any build-time conn use. This also imports loop.py, so a
    # broken import there fails the gate instead of only a manual run.
    agent = build_agent(conn=object())

    # _function_toolset is pydantic-ai's private store of the tools passed to
    # Agent(...); there is no public name-enumeration API, and its .tools dict is
    # keyed by the name the model calls.
    assert set(agent._function_toolset.tools) == {"sql_query", "vector_search", "role_detail"}


def test_build_prose_agent_is_offline_and_has_no_tools() -> None:
    # Both locales default to Qwen, resolved against the China endpoint with a
    # placeholder key, so this builds without ALIBABA_API_KEY (defer/no network).
    agent = build_prose_agent(lang="en")
    assert set(agent._function_toolset.tools) == set()


def test_resolve_model_passes_through_deepseek() -> None:
    # Non-special providers stay a plain string, inferred lazily so no key is used.
    assert resolve_model("deepseek:deepseek-v4-flash") == "deepseek:deepseek-v4-flash"


def test_resolve_model_alibaba_builds_offline() -> None:
    # alibaba:* resolves to a concrete Model bound to the China endpoint, and does
    # so without ALIBABA_API_KEY set (the placeholder key keeps CI network-free).
    from pydantic_ai.models import Model

    assert isinstance(resolve_model("alibaba:qwen3.8-flash"), Model)


def test_prose_model_settings_temperature() -> None:
    # Qwen/DeepSeek pin temperature=0 for determinism.
    assert prose_model_settings("alibaba:qwen3.8-flash").get("temperature") == 0
    assert prose_model_settings("deepseek:deepseek-v4-flash").get("temperature") == 0


def test_default_stage2_model_per_locale() -> None:
    # Both locales run Qwen flash now; the per-locale split is kept so a locale can
    # be repointed at its own model without touching the call sites.
    assert default_stage2_model("ru") == DEFAULT_STAGE2_RU_MODEL == "alibaba:qwen3.8-flash"
    assert default_stage2_model("en") == DEFAULT_STAGE2_MODEL == "alibaba:qwen3.8-flash"


def test_assemble_fills_placeholders_and_attaches_labels() -> None:
    # Values from gold_40_candidates.json row 1 (hn 48747990, We The Flywheel /
    # Agentic Engineer). The prose wording is language-neutral here because
    # translation is stage 2's job — assemble only fills numbers and labels.
    payload = Payload(
        tables=[TableSpec(kind="bars", source="skill_frequency", rows=[["claude code", 1604]])],
        quotes=[
            Quote(
                id=1,
                company="We The Flywheel",
                title="Agentic Engineer",
                source="hn",
                text="ship real software with AI coding agents",
            )
        ],
        facts=[Fact(key="top_skill_roles", value=1604)],
    )
    prose = Prose(
        headline="claude code leads with {top_skill_roles} roles.",
        key_finding="claude code appears in {top_skill_roles} roles [1].",
    )

    en = assemble(payload, prose, "en")
    ru = assemble(payload, prose, "ru")

    assert en.headline == "claude code leads with 1,604 roles."
    assert ru.headline == "claude code leads with 1 604 roles."
    assert en.tables[0].title == "Most-common skills"
    assert ru.tables[0].title == "Самые частые навыки"
    assert en.tables[0].headers == []
    assert en.quotes[0].company == "We The Flywheel"


def test_assemble_refusal_returns_localized_message() -> None:
    # A refused payload has no prose to fill and no data to render: the headline
    # is the code-owned localized refusal, and the flag rides the wire so the
    # frontend/eval can tell a refusal from an empty answer.
    payload = Payload(refused=True)
    prose = Prose(headline="ignored", key_finding="ignored")
    en = assemble(payload, prose, "en")
    ru = assemble(payload, prose, "ru")

    assert en.refused is True and ru.refused is True
    assert (en.headline, en.key_finding, en.tables, en.quotes) == (REFUSAL["en"], "", [], [])
    assert (ru.headline, ru.key_finding, ru.tables, ru.quotes) == (REFUSAL["ru"], "", [], [])


def test_compose_skips_prose_on_refusal() -> None:
    # A refused question must not spend a stage-2 request. The prose agent is a
    # stub whose run() raises, so if compose called it the test would blow up.
    class RaisingProse:
        async def run(self, *_args, **_kwargs) -> None:
            raise AssertionError("prose must not run for a refusal")

    answer = asyncio.run(compose(Payload(refused=True), "en", RaisingProse()))  # type: ignore[arg-type]
    assert answer.refused is True
    assert answer.headline == REFUSAL["en"]


def test_fill_quote_details_attaches_link_and_description(monkeypatch) -> None:
    # The posting link is code-attached from the (source, company, title) match,
    # never model-copied: a hit fills url + description, a miss (no structured
    # row names that triple) leaves the quote untouched — a missing link, not a
    # failed answer.
    from techanizer.storage.role import QuoteLink

    def fake_link(conn, source: str, company: str | None, title: str | None, text: str = ""):
        if (source, company, title) == ("hn", "Acme", "ML"):
            return QuoteLink("https://news.ycombinator.com/item?id=7", "builds models")
        return None

    monkeypatch.setattr("techanizer.storage.role.quote_link", fake_link)
    quotes = [
        Quote(id=1, source="hn", company="Acme", title="ML", text="x"),
        Quote(id=2, source="hn", company="Ghost", title="X", text="y"),
    ]

    fill_quote_details(object(), quotes)

    assert quotes[0].url == "https://news.ycombinator.com/item?id=7"
    assert quotes[0].description == "builds models"
    assert quotes[1].url is None and quotes[1].description is None
