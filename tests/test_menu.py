import os

import psycopg
import pytest
from dotenv import load_dotenv

from jobmarket_agent.agent.menu import build_payload, question_for, validate_params
from jobmarket_agent.storage import analytics, role
from jobmarket_agent.storage.embed import EMBED_DIM

load_dotenv()


# --- offline: param validation + question rendering -------------------------


def test_validate_params_rejects_missing_phrase() -> None:
    with pytest.raises(ValueError):
        validate_params("similar_roles", {})


def test_validate_params_rejects_scope_without_filter() -> None:
    # scope=seniority names a filter the request doesn't supply.
    with pytest.raises(ValueError):
        validate_params("skills", {"scope": "seniority"})


def test_validate_params_accepts_scope_with_filter() -> None:
    validate_params("skills", {"scope": "remote", "remote": "hybrid"})
    validate_params("skills", {"scope": "overall"})
    validate_params("salary_by_currency", {})


def test_question_for_locales() -> None:
    assert question_for("skills", {}, "en") == "Which skills appear in the most roles?"
    assert question_for("skills", {}, "ru") == "Какие навыки упоминаются в наибольшем числе ролей?"
    assert (
        question_for("skills", {"scope": "seniority", "seniority": "senior"}, "en")
        == "Which skills are most common among senior roles?"
    )
    assert (
        question_for("top_salaries", {"currency": "RUB"}, "en")
        == "Which are the highest-paid roles in RUB?"
    )
    assert (
        question_for("similar_roles", {"phrase": "rust"}, "en")
        == 'Which roles are most similar to "rust"?'
    )


@pytest.fixture(scope="module")
def eval_conn():
    db_url = os.environ.get("EVAL_DATABASE_URL")
    if not db_url:
        pytest.skip("EVAL_DATABASE_URL unset")
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT role FROM db_meta")
            row = cur.fetchone()
        assert row is not None and row[0] == "eval", "not the frozen snapshot"
        yield conn


# --- builders against the frozen snapshot -----------------------------------


def test_build_payload_skills_overall(eval_conn) -> None:
    payload = build_payload("skills", {}, eval_conn)
    assert len(payload.tables) == 1
    table = payload.tables[0]
    assert table.kind == "bars"
    assert table.source == "skill_frequency"
    assert 0 < len(table.rows) <= 10
    # rows are [skill, roles]; the top row matches the corpus-wide top skill.
    assert table.rows[0][0] == analytics.skill_frequency(eval_conn, limit=1)[0].skill
    assert payload.facts and payload.facts[0].key == "top_skill_roles"
    assert payload.facts[0].value == table.rows[0][1]
    assert payload.quotes == []  # aggregates carry no verbatim quotes


def test_build_payload_skills_by_seniority(eval_conn) -> None:
    payload = build_payload("skills", {"scope": "seniority", "seniority": "senior"}, eval_conn)
    assert payload.tables[0].source == "skill_frequency_by_seniority"
    assert payload.tables[0].kind == "bars"
    assert payload.tables[0].rows


def test_build_payload_salary_by_seniority(eval_conn) -> None:
    payload = build_payload("salary_by_seniority", {"currency": "USD"}, eval_conn)
    table = payload.tables[0]
    assert table.source == "salary_by_seniority"
    assert table.kind == "table"
    # rows are [seniority, roles, median_max_monthly], three cells each.
    assert table.rows and all(len(r) == 3 for r in table.rows)
    assert payload.facts and payload.facts[0].key == "top_median"


def test_build_payload_salary_by_currency(eval_conn) -> None:
    payload = build_payload("salary_by_currency", {}, eval_conn)
    table = payload.tables[0]
    assert table.source == "salary_by_currency"
    assert table.kind == "table"
    assert table.rows and all(len(r) == 3 for r in table.rows)
    assert payload.facts and payload.facts[0].key == "top_currency_roles"


def test_build_payload_top_salaries_quotes_verbatim(eval_conn) -> None:
    payload = build_payload("top_salaries", {"currency": "RUB", "limit": 3}, eval_conn)
    table = payload.tables[0]
    assert table.source == "top_salaries"
    # rows are [company, title, salary].
    assert all(len(r) == 3 for r in table.rows)
    assert payload.facts and payload.facts[0].key == "top_salary"

    top = analytics.top_salaries(eval_conn, "RUB", 3)
    assert payload.quotes, "role-level items carry verbatim quotes"
    for i, q in enumerate(payload.quotes):
        assert q.id == i + 1
        assert q.salary_currency == "RUB"
    # The first quote is the top row, and its text is a verbatim substring of
    # that role's source_quotes — the same check the agent eval's verbatim
    # grader applies.
    first = payload.quotes[0]
    assert (first.company, first.title) == (top[0].company, top[0].title)
    detail = role.role_detail(eval_conn, top[0].raw_posting_id, top[0].role_index)
    assert detail is not None
    assert any(first.text in h for h in detail.source_quotes.values())


def _fake_embed(texts: list[str]) -> list[list[float]]:
    # Non-zero constant vector: a zero vector makes pgvector cosine distance NaN.
    return [[1.0] * EMBED_DIM for _ in texts]


def test_build_payload_similar_roles(eval_conn, monkeypatch) -> None:
    monkeypatch.setattr("jobmarket_agent.storage.vector_search.embed", _fake_embed)
    payload = build_payload(
        "similar_roles", {"phrase": "distributed systems engineer", "limit": 5}, eval_conn
    )
    table = payload.tables[0]
    assert table.source == "vector_search"
    # rows are [company, title, seniority, similarity].
    assert all(len(r) == 4 for r in table.rows)
    # similarity is a retrieval metric, never a market fact.
    assert payload.facts and payload.facts[0].key == "n_matches"
    assert payload.facts[0].value == len(table.rows)
    assert payload.quotes
