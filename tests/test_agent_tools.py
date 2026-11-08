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
    "url",
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
    # No DB: the factory must stay pure (bare object()), and the loosened filter
    # args (str | list[str] | None) must survive docstring resolution.
    fn = make_vector_search(object())
    td = Tool(fn).tool_def

    props = td.parameters_json_schema["properties"]
    assert set(props) == {"query", "limit", "seniority", "remote"}
    # seniority/remote accept a bare string, a list, or null: a tool-caller that
    # sends the wrong shape degrades to "no filter" instead of failing validation
    # and burning the tool's single retry (Qwen does this often).
    for key in ("seniority", "remote"):
        kinds = {branch.get("type") for branch in props[key]["anyOf"]}
        assert kinds == {"string", "array", "null"}

    desc = (td.description or "").lower()
    for fact in ("similarity", "monthly"):
        assert fact in desc


def test_vector_search_coerces_filter_args() -> None:
    from techanizer.agent.tools import _REMOTE_VALUES, _SENIORITY_VALUES, _coerce_filter

    # Bare string -> list; out-of-enum values dropped; all-invalid -> None (no filter).
    assert _coerce_filter("senior", _SENIORITY_VALUES) == ["senior"]
    assert _coerce_filter(["senior", "bogus"], _SENIORITY_VALUES) == ["senior"]
    assert _coerce_filter(["bogus"], _SENIORITY_VALUES) is None
    assert _coerce_filter(None, _REMOTE_VALUES) is None
    assert _coerce_filter("remote", _REMOTE_VALUES) == ["remote"]


def test_vector_search_shape(eval_conn, monkeypatch) -> None:
    monkeypatch.setattr("techanizer.storage.vector_search.embed", _fake_embed)
    rows = make_vector_search(eval_conn)(query="distributed systems engineer", limit=5)
    assert 0 < len(rows) <= 5
    for r in rows:
        assert set(r) == _SEARCH_HIT_KEYS
    # Ranking is now fused (dense + lexical), so similarity is not monotonic;
    # only shape and filters are asserted here.


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


def test_quote_link_resolves_url_from_copied_fields(eval_conn) -> None:
    # The link must resolve from exactly the fields a quote carries — source,
    # company, title — because that is what the model copies verbatim. Pick a
    # real row's triple rather than pinning an id, so re-snapshots don't break it.
    from techanizer.storage.role import quote_link

    with eval_conn.cursor() as cur:
        cur.execute(
            "SELECT r.source, s.company, s.title FROM structured_postings s "
            "JOIN raw_postings r ON r.id = s.raw_posting_id "
            "WHERE s.company IS NOT NULL AND s.title IS NOT NULL "
            "ORDER BY s.raw_posting_id LIMIT 1"
        )
        row = cur.fetchone()
    assert row is not None

    link = quote_link(eval_conn, *row)

    assert link is not None
    assert link.url is not None  # every source resolves a URL


def test_quote_link_missing_triple_returns_none(eval_conn) -> None:
    from techanizer.storage.role import quote_link

    assert quote_link(eval_conn, "hn", "Ghost Co", "Nothing") is None


def test_quote_link_text_fallback(eval_conn) -> None:
    # A quote whose company/title the model left empty still links via its
    # verbatim span (>= MIN_QUOTE_TEXT_MATCH) within the source.
    from techanizer.storage.role import quote_link

    with eval_conn.cursor() as cur:
        cur.execute(
            "SELECT r.source, q.v FROM structured_postings s "
            "JOIN raw_postings r ON r.id = s.raw_posting_id, "
            "     jsonb_each_text(s.source_quotes) AS q(k, v) "
            "WHERE length(q.v) >= 40 AND s.company IS NOT NULL AND s.title IS NOT NULL "
            "ORDER BY s.raw_posting_id LIMIT 1"
        )
        row = cur.fetchone()
    assert row is not None

    link = quote_link(eval_conn, row[0], None, None, row[1])

    assert link is not None
    assert link.url is not None
