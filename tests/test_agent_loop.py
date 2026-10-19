from jobmarket_agent.agent.loop import assemble, build_agent, build_prose_agent
from jobmarket_agent.agent.schema import Fact, Payload, Prose, Quote, TableSpec


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
    # defer_model_check=True, same as stage 1, so this builds without a key.
    agent = build_prose_agent(lang="ru")
    assert set(agent._function_toolset.tools) == set()


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
