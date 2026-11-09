import os
from datetime import date
from typing import cast
from uuid import uuid4

import psycopg
import pytest
import requests

from techanizer.ingestion.load import LoadStats
from techanizer.ops.run_log import begin_run, end_run
from techanizer.storage import embed as embed_module
from techanizer.storage.analytics import (
    MonthSalary,
    MonthSkill,
    TopSalary,
    monthly_salary,
    monthly_skill_frequency,
    rank_top_salaries,
)
from techanizer.storage.embed import to_vector_text
from techanizer.storage.vector_search import (
    SearchHit,
    expand_query,
    lexical_terms,
    lexical_tsquery,
    reciprocal_rank_fusion,
)


# Serializer output format only — the psycopg round-trip is already proven by
# the 1,660 live vectors. Scientific notation is the case DECISIONS: storage
# calls out as verified-against-Python's-str-repr.
@pytest.mark.parametrize(
    ("vec", "expected"),
    [
        ([1.0, 2.5, 3.14], "[1.0,2.5,3.14]"),
        ([1e-05], "[1e-05]"),
        ([-1.5, 0.0], "[-1.5,0.0]"),
    ],
)
def test_to_vector_text(vec: list[float], expected: str) -> None:
    assert to_vector_text(vec) == expected


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


# DashScope keys each embedding by text_index and carries the retrieval role
# (query vs document) as a parameter — the pair bge-m3's QUERY_INSTRUCTION
# used to encode. The parser must restore request order and forward the role.
@pytest.mark.parametrize(
    ("text_type", "expected_param"),
    [("document", "document"), ("query", "query")],
)
def test_embed_request_contract(
    monkeypatch: pytest.MonkeyPatch, text_type: str, expected_param: str
) -> None:
    # embed() checks the key before the (faked) request; CI has no
    # ALIBABA_API_KEY, so set a dummy one to keep the test hermetic.
    monkeypatch.setenv("ALIBABA_API_KEY", "test-key")
    monkeypatch.setattr(embed_module, "EMBED_DIM", 2)
    captured: dict = {}

    def fake_post(
        url: str, headers: dict | None = None, json: dict | None = None, timeout: int | None = None
    ) -> _FakeResponse:
        captured.update(json or {})
        return _FakeResponse(
            {
                "output": {
                    "embeddings": [
                        {"text_index": 1, "embedding": [0.4, 0.4]},
                        {"text_index": 0, "embedding": [0.1, 0.2]},
                    ]
                }
            }
        )

    monkeypatch.setattr(embed_module.requests, "post", fake_post)
    assert embed_module.embed(["b", "a"], text_type=text_type) == [[0.1, 0.2], [0.4, 0.4]]
    assert captured["input"] == {"texts": ["b", "a"]}
    assert captured["parameters"] == {"text_type": expected_param, "dimension": 2}


def test_embed_rejects_oversized_batch() -> None:
    with pytest.raises(ValueError, match="10 texts"):
        embed_module.embed(["x"] * 11)


def test_embed_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALIBABA_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ALIBABA_API_KEY"):
        embed_module.embed(["x"])


# The weekly scheduler runs unattended: a transient TLS failure (seen live,
# 2026-11-09) is retried, not fatal.
def test_embed_retries_transient_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALIBABA_API_KEY", "test-key")
    monkeypatch.setattr(embed_module, "EMBED_DIM", 2)
    monkeypatch.setattr(embed_module.time, "sleep", lambda _s: None)
    calls: list[int] = []

    def flaky_post(
        url: str, headers: dict | None = None, json: dict | None = None, timeout: int | None = None
    ) -> _FakeResponse:
        calls.append(1)
        if len(calls) < 3:
            raise requests.ConnectionError("transient TLS blip")
        return _FakeResponse(
            {"output": {"embeddings": [{"text_index": 0, "embedding": [0.1, 0.2]}]}}
        )

    monkeypatch.setattr(embed_module.requests, "post", flaky_post)
    assert embed_module.embed(["a"]) == [[0.1, 0.2]]
    assert len(calls) == 3


def test_embed_raises_after_exhausted_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALIBABA_API_KEY", "test-key")
    monkeypatch.setattr(embed_module.time, "sleep", lambda _s: None)

    def dead_post(
        url: str, headers: dict | None = None, json: dict | None = None, timeout: int | None = None
    ) -> _FakeResponse:
        raise requests.ConnectionError("down for good")

    monkeypatch.setattr(embed_module.requests, "post", dead_post)
    with pytest.raises(requests.ConnectionError):
        embed_module.embed(["a"])


# Suffixes are data-driven from eval_retrieval: the ambiguous tokens that
# bge-m3 mis-encodes when bare, and the suffix that anchors them back.
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("kafka event streaming", "kafka event streaming stream processing"),
        ("Kafka Event Streaming", "Kafka Event Streaming stream processing"),
        ("postgresql database engineer", "postgresql database engineer relational database"),
        ("postgres database", "postgres database relational database"),
        ("solidity smart contract", "solidity smart contract ethereum blockchain"),
        ("rust systems programmer", "rust systems programmer"),
        ("apache kafka stream processing", "apache kafka stream processing"),
    ],
)
def test_expand_query(query: str, expected: str) -> None:
    assert expand_query(query) == expected


# OR, not AND: a role whose stack has only "kafka" must still match the query
# "kafka event streaming". Terms mirror the 'simple' parser's split (non-word
# chars), lowercased, so the exact-token leg needs any one of them.
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("kafka event streaming", "kafka | event | streaming"),
        ("Kafka Event Streaming", "kafka | event | streaming"),
        ("postgresql database engineer", "postgresql | database | engineer"),
        ("golang backend engineer", "golang | backend | engineer"),
        ("data engineer", "data | engineer"),
        ("c++ / rust", "c | rust"),
    ],
)
def test_lexical_tsquery(query: str, expected: str) -> None:
    assert lexical_tsquery(query) == expected


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Kafka Event Streaming", ["kafka", "event", "streaming"]),
        ("c++ / rust", ["c", "rust"]),
        ("  ", []),
    ],
)
def test_lexical_terms(query: str, expected: list[str]) -> None:
    assert lexical_terms(query) == expected


def _hit(raw_posting_id: int, similarity: float = 1.0) -> SearchHit:
    return SearchHit(
        raw_posting_id,
        0,
        None,
        None,
        None,
        "unknown",
        "unknown",
        "unknown",
        [],
        None,
        None,
        None,
        None,
        "hn",
        "",
        similarity,
    )


# RRF (k=60) is the fusion contract: a doc present in both legs accumulates
# 1/(k+rank) per leg, so an exact-token match sitting low in the dense list
# still outranks a semantically-close doc the lexical leg never matched.
def test_reciprocal_rank_fusion_ordering() -> None:
    dense = [_hit(1), _hit(2)]
    lexical = [_hit(2), _hit(3)]
    fused = reciprocal_rank_fusion(dense, lexical, limit=10)
    assert [h.raw_posting_id for h in fused] == [2, 1, 3]


def test_reciprocal_rank_fusion_rescues_exact_match() -> None:
    # kafka-shaped: the dense rank-1 doc is wrong, the exact match sits at dense
    # rank 2 but lex rank 1; fusion must surface the exact match first.
    dense = [_hit(10), _hit(20)]
    lexical = [_hit(20)]
    fused = reciprocal_rank_fusion(dense, lexical, limit=10)
    assert fused[0].raw_posting_id == 20


def test_reciprocal_rank_fusion_limit() -> None:
    dense = [_hit(i) for i in range(10)]
    fused = reciprocal_rank_fusion(dense, [], limit=3)
    assert [h.raw_posting_id for h in fused] == [0, 1, 2]


def test_reciprocal_rank_fusion_keeps_dense_similarity() -> None:
    # A key in both legs keeps the dense leg's hit (and its similarity), so the
    # reported number stays the cosine score, not a fused value.
    dense = [_hit(1, similarity=0.9), _hit(2, similarity=0.7)]
    lexical = [_hit(2, similarity=0.1)]
    fused = reciprocal_rank_fusion(dense, lexical, limit=10)
    by_key = {h.raw_posting_id: h.similarity for h in fused}
    assert by_key[2] == 0.7


def _top(raw_posting_id: int, salary_min: int | None, salary_max: int | None) -> TopSalary:
    return TopSalary(raw_posting_id, 0, None, None, None, "unknown", salary_min, salary_max, "RUB")


# Row values are the real snapshot rows behind the salary-ranking bug: 13489
# ("Экосистема тенниса", min-only 500k) ties with 13682 (BASIS, max-only 500k);
# 13611 ("Рекрутер", min-only 410k) must outrank 13857 (Advers, 350-400k).
@pytest.mark.parametrize(
    ("rows", "limit", "expected_ids"),
    [
        # NULL-max rows rank at their stated floor, not dropped or last.
        ([_top(13857, 350000, 400000), _top(13611, 410000, None)], 10, [13611, 13857]),
        # Tie at 500k (one NULL max, one NULL min): stable key breaks it.
        ([_top(13682, None, 500000), _top(13489, 500000, None)], 10, [13489, 13682]),
        # The full snapshot podium, shuffled: tie first, then the 490k ceilings.
        (
            [
                _top(13630, 300000, 490000),
                _top(13611, 410000, None),
                _top(13682, None, 500000),
                _top(13489, 500000, None),
                _top(13590, 300000, 490000),
            ],
            10,
            [13489, 13682, 13590, 13630, 13611],
        ),
        # Limit applies after ranking, not before.
        ([_top(13611, 410000, None), _top(13489, 500000, None)], 1, [13489]),
    ],
)
def test_rank_top_salaries(rows: list[TopSalary], limit: int, expected_ids: list[int]) -> None:
    ranked = rank_top_salaries(rows, limit)
    assert [r.raw_posting_id for r in ranked] == expected_ids


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


@pytest.fixture(scope="module")
def writable_conn():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        pytest.skip("DATABASE_URL unset")
    with psycopg.connect(db_url) as conn:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute("SELECT role FROM db_meta")
            row = cur.fetchone()
        if row is not None and row[0] == "live":
            pytest.skip("refusing to touch the live DB")
        yield conn


def _min_month(conn) -> date:
    with conn.cursor() as cur:
        cur.execute("SELECT date_trunc('month', min(posted_at))::date FROM raw_postings")
        row = cur.fetchone()
        assert row is not None and row[0] is not None
        return cast(date, row[0])


def test_monthly_skill_frequency_shape(eval_conn) -> None:
    rows = monthly_skill_frequency(eval_conn)
    assert rows
    for r in rows:
        assert isinstance(r, MonthSkill)
        assert r.roles > 0 and r.postings > 0
    # Trend reads full history: the earliest bucket is the corpus's first month,
    # which a windowed query would have trimmed away.
    assert min(r.month for r in rows) == _min_month(eval_conn)
    # Ordered by month, then roles DESC within a month.
    assert all(rows[i].month <= rows[i + 1].month for i in range(len(rows) - 1))


def test_monthly_salary_shape(eval_conn) -> None:
    rows = monthly_salary(eval_conn)
    assert rows
    for r in rows:
        assert isinstance(r, MonthSalary)
        assert r.n > 0
        # NULL currency keeps coverage (n) but withholds a median, same rule as
        # salary_by_currency.
        if r.currency is None:
            assert r.median_max_monthly is None
    assert min(r.month for r in rows) == _min_month(eval_conn)
    assert all(rows[i].month <= rows[i + 1].month for i in range(len(rows) - 1))


def test_run_log_roundtrip(writable_conn) -> None:
    run_id = uuid4()
    begin_run(writable_conn, run_id, "hn")
    end_run(writable_conn, run_id, "hn", 10, LoadStats(3, 2, 5, 0))
    with writable_conn.cursor() as cur:
        cur.execute(
            "SELECT fetched, inserted, updated, unchanged, failed, error, ended_at "
            "FROM ingestion_runs WHERE run_id = %s AND source = 'hn'",
            (run_id,),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[:5] == (10, 3, 2, 5, 0)
    assert row[5] is None  # error
    assert row[6] is not None  # ended_at stamped


def test_run_log_error_row(writable_conn) -> None:
    run_id = uuid4()
    begin_run(writable_conn, run_id, "web3")
    end_run(writable_conn, run_id, "web3", None, error="boom")
    with writable_conn.cursor() as cur:
        cur.execute(
            "SELECT fetched, inserted, updated, unchanged, failed, error "
            "FROM ingestion_runs WHERE run_id = %s AND source = 'web3'",
            (run_id,),
        )
        row = cur.fetchone()
    assert row is not None
    assert row == (None, None, None, None, None, "boom")
