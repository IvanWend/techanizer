import os

import psycopg
import pytest
from dotenv import load_dotenv
from pydantic_ai import Tool

from techanizer.agent.tools import make_role_detail, make_sql_query, make_vector_search
from techanizer.storage.embed import EMBED_DIM

load_dotenv()


# The tool wraps vector_search.run, which embeds the query over Ollama. The
# execution tests patch embed with a constant non-zero vector (zero would make
# pgvector's cosine distance undefined) so they run against the frozen snapshot
# offline; they assert shape and filter behaviour, not semantic ranking.
def _fake_embed(texts: list[str]) -> list[list[float]]:
    return [[1.0] * EMBED_DIM for _ in texts]


_SEARCH_HIT_KEYS = {
    "raw_posting_id",
    "role_index",
    "title",
    "company",
    "location",
    "seniority",
    "remote_policy",
    "employment_type",
    "stack",
    "salary_min",
    "salary_max",
    "salary_currency",
    "description",
    "source",
    "external_id",
    "similarity",
}

_ROLE_DETAIL_KEYS = {
    "raw_posting_id",
    "role_index",
    "company",
    "title",
    "location",
    "seniority",
    "remote_policy",
    "employment_type",
    "stack",
    "salary_min",
    "salary_max",
    "salary_currency",
    "description",
    "source_quotes",
    "source",
    "external_id",
}


def test_sql_query_schema_contract() -> None:
    # No DB: proves the whitelist + teaching facts survive docstring resolution,
    # and that the factory is pure (a bare object AttributeErrors on any conn use).
    fn = make_sql_query(object())
    td = Tool(fn).tool_def

    props = td.parameters_json_schema["properties"]
    assert props["query"]["enum"] == [
        "skill_frequency",
        "salary_by_currency",
        "salary_by_seniority",
        "top_salaries",
    ]
    assert set(props) == {"query", "limit", "currency"}

    desc = (td.description or "").lower()
    for fact in ("monthly", "postings", "currency"):
        assert fact in desc


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


def test_skill_frequency_shape(eval_conn) -> None:
    rows = make_sql_query(eval_conn)(query="skill_frequency", limit=3)
    assert 0 < len(rows) <= 3
    for r in rows:
        assert set(r) == {"skill", "roles", "postings"}
    # SKILL_FREQUENCY_SQL orders by roles DESC, so the list is non-increasing.
    assert all(rows[i]["roles"] >= rows[i + 1]["roles"] for i in range(len(rows) - 1))


def test_salary_by_currency_shape(eval_conn) -> None:
    rows = make_sql_query(eval_conn)(query="salary_by_currency")
    assert rows
    for r in rows:
        assert set(r) == {"currency", "n", "median_max_monthly"}


def test_salary_by_seniority_shape(eval_conn) -> None:
    rows = make_sql_query(eval_conn)(query="salary_by_seniority", currency="USD")
    assert rows
    for r in rows:
        assert set(r) == {"seniority", "roles", "with_salary", "median_max_monthly"}


_TOP_SALARY_KEYS = {
    "raw_posting_id",
    "role_index",
    "company",
    "title",
    "location",
    "seniority",
    "salary_min",
    "salary_max",
    "salary_currency",
}


def test_top_salaries_shape(eval_conn) -> None:
    rows = make_sql_query(eval_conn)(query="top_salaries", currency="RUB", limit=5)
    assert 0 < len(rows) <= 5
    for r in rows:
        assert set(r) == _TOP_SALARY_KEYS
        assert r["salary_currency"] == "RUB"
    # rank_top_salaries orders by COALESCE(salary_max, salary_min) DESC with the
    # key breaking ties, so the figures are non-increasing.
    figures = [r["salary_max"] if r["salary_max"] is not None else r["salary_min"] for r in rows]
    assert all(figures[i] >= figures[i + 1] for i in range(len(figures) - 1))


def test_vector_search_schema_contract() -> None:
    # No DB: the whitelist (seniority/remote Literals) must survive docstring
    # resolution as an enum, and the factory must stay pure (bare object()).
    fn = make_vector_search(object())
    td = Tool(fn).tool_def

    props = td.parameters_json_schema["properties"]
    assert set(props) == {"query", "limit", "seniority", "remote"}
    # list[Literal] | None resolves to anyOf[array-with-enum, null].
    assert props["seniority"]["anyOf"][0]["items"]["enum"] == [
        "intern",
        "junior",
        "mid",
        "senior",
        "staff+",
    ]
    assert props["remote"]["anyOf"][0]["items"]["enum"] == [
        "remote",
        "hybrid",
        "onsite",
    ]

    desc = (td.description or "").lower()
    for fact in ("similarity", "monthly"):
        assert fact in desc


def test_vector_search_shape(eval_conn, monkeypatch) -> None:
    monkeypatch.setattr("techanizer.storage.vector_search.embed", _fake_embed)
    rows = make_vector_search(eval_conn)(query="distributed systems engineer", limit=5)
    assert 0 < len(rows) <= 5
    for r in rows:
        assert set(r) == _SEARCH_HIT_KEYS
    # SEARCH_SQL orders by cosine distance ascending, so similarity is
    # non-increasing across the result.
    assert all(rows[i]["similarity"] >= rows[i + 1]["similarity"] for i in range(len(rows) - 1))


def test_vector_search_seniority_filter(eval_conn, monkeypatch) -> None:
    monkeypatch.setattr("techanizer.storage.vector_search.embed", _fake_embed)
    rows = make_vector_search(eval_conn)(
        query="frontend engineer", limit=10, seniority=["senior", "staff+"]
    )
    assert rows
    assert {r["seniority"] for r in rows} <= {"senior", "staff+"}


def test_role_detail_schema_contract() -> None:
    # No DB: the two int params must resolve cleanly and the factory stay pure
    # (a bare object() AttributeErrors on any conn use).
    fn = make_role_detail(object())
    td = Tool(fn).tool_def

    props = td.parameters_json_schema["properties"]
    assert set(props) == {"raw_posting_id", "role_index"}
    assert props["raw_posting_id"]["type"] == "integer"
    assert props["role_index"]["type"] == "integer"

    desc = (td.description or "").lower()
    for fact in ("source_quotes", "raw_posting_id", "monthly"):
        assert fact in desc


def test_role_detail_shape(eval_conn) -> None:
    # Key comes from the snapshot itself, not a pinned number, so a re-snapshot
    # does not break the test.
    with eval_conn.cursor() as cur:
        cur.execute(
            "SELECT raw_posting_id, role_index FROM structured_postings "
            "ORDER BY raw_posting_id, role_index LIMIT 1"
        )
        row = cur.fetchone()
    assert row is not None
    rid, ridx = row[0], row[1]

    result = make_role_detail(eval_conn)(raw_posting_id=rid, role_index=ridx)
    assert result is not None
    assert set(result) == _ROLE_DETAIL_KEYS
    assert result["raw_posting_id"] == rid
    assert result["role_index"] == ridx
    assert isinstance(result["source_quotes"], dict)


def test_role_detail_missing(eval_conn) -> None:
    assert make_role_detail(eval_conn)(raw_posting_id=-1, role_index=-1) is None
