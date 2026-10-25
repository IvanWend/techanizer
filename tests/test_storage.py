import os
from datetime import date
from typing import cast
from uuid import uuid4

import psycopg
import pytest

from jobmarket_agent.ingestion.load import LoadStats
from jobmarket_agent.ops.run_log import begin_run, end_run
from jobmarket_agent.storage.analytics import (
    MonthSalary,
    MonthSkill,
    TopSalary,
    monthly_salary,
    monthly_skill_frequency,
    rank_top_salaries,
)
from jobmarket_agent.storage.embed import to_vector_text


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
