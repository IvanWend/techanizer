import argparse
import logging
import os
from datetime import date
from typing import NamedTuple

import psycopg
from dotenv import load_dotenv

from jobmarket_agent.ingestion.retention import WINDOW_DAYS

load_dotenv()

logger = logging.getLogger(__name__)


class SkillCount(NamedTuple):
    skill: str
    roles: int
    postings: int


class CurrencySalary(NamedTuple):
    currency: str | None
    n: int
    median_max_monthly: int | None


class SenioritySalary(NamedTuple):
    seniority: str
    roles: int
    with_salary: int
    median_max_monthly: int | None


class TopSalary(NamedTuple):
    raw_posting_id: int
    role_index: int
    company: str | None
    title: str | None
    location: str | None
    seniority: str
    salary_min: int | None
    salary_max: int | None
    salary_currency: str | None


class MonthSkill(NamedTuple):
    month: date
    skill: str
    roles: int
    postings: int


class MonthSalary(NamedTuple):
    month: date
    currency: str | None
    n: int
    median_max_monthly: int | None


# The live-agent window (retention.WINDOW_DAYS), applied only when db_meta.role is
# 'live': the frozen eval snapshot serves its whole corpus as current, and its
# role is 'eval'. A scalar subquery keeps the predicate self-contained per query
# rather than a per-call role lookup in Python.
WINDOW_SQL = (
    "((SELECT role FROM db_meta) <> 'live' "
    "OR r.posted_at >= now() - make_interval(days => %(days)s))"
)

SKILL_FREQUENCY_SQL = f"""
SELECT unnest(s.stack) AS skill,
       count(*) AS roles,
       count(DISTINCT s.raw_posting_id) AS postings
FROM structured_postings s
JOIN raw_postings r ON r.id = s.raw_posting_id
WHERE {WINDOW_SQL}
GROUP BY 1
ORDER BY roles DESC, skill
LIMIT %(limit)s
"""

# The two scoped skill queries: the same shape as SKILL_FREQUENCY_SQL with one
# equality filter. They back the menu's "most-common skills" scope selector
# (overall / by-seniority / by-remote) — code filters, never the model (the
# retired stage 1 would read the corpus-wide table and ignore the scope).
SKILL_FREQUENCY_BY_SENIORITY_SQL = f"""
SELECT unnest(s.stack) AS skill,
       count(*) AS roles,
       count(DISTINCT s.raw_posting_id) AS postings
FROM structured_postings s
JOIN raw_postings r ON r.id = s.raw_posting_id
WHERE s.seniority = %(seniority)s
  AND {WINDOW_SQL}
GROUP BY 1
ORDER BY roles DESC, skill
LIMIT %(limit)s
"""

SKILL_FREQUENCY_BY_REMOTE_SQL = f"""
SELECT unnest(s.stack) AS skill,
       count(*) AS roles,
       count(DISTINCT s.raw_posting_id) AS postings
FROM structured_postings s
JOIN raw_postings r ON r.id = s.raw_posting_id
WHERE s.remote_policy = %(remote)s
  AND {WINDOW_SQL}
GROUP BY 1
ORDER BY roles DESC, skill
LIMIT %(limit)s
"""

# NULL currency is a mix of whatever currencies the source never stated, so a
# median across it is neither currency. FILTER keeps the row (n = coverage)
# while withholding a number that cannot mean anything.
SALARY_BY_CURRENCY_SQL = f"""
SELECT s.salary_currency,
       count(*) AS n,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY s.salary_max)
           FILTER (WHERE s.salary_currency IS NOT NULL)::int AS median_max_monthly
FROM structured_postings s
JOIN raw_postings r ON r.id = s.raw_posting_id
WHERE s.salary_max IS NOT NULL
  AND {WINDOW_SQL}
GROUP BY 1
ORDER BY n DESC, s.salary_currency
"""

# The currency filter is not optional: percentile_cont over a mixed-currency
# list returns a midpoint that is neither currency (mid = 49 RUB + 41 USD landed
# at 80,500/mo, above senior's USD median). median_max ignores NULL salary_max,
# so with_salary still shows coverage even though salary_max IS NOT NULL is absent.
SALARY_BY_SENIORITY_SQL = f"""
SELECT s.seniority,
       count(*) AS roles,
       count(s.salary_max) AS with_salary,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY s.salary_max)::int AS median_max_monthly
FROM structured_postings s
JOIN raw_postings r ON r.id = s.raw_posting_id
WHERE s.salary_currency = %(currency)s
  AND {WINDOW_SQL}
GROUP BY s.seniority
ORDER BY median_max_monthly DESC NULLS LAST, s.seniority
"""

# The currency filter is not optional, same as the seniority split: a ranking
# across mixed currencies orders magnitudes, not salaries (500,000 RUB beats
# 25,000 USD numerically). Rows with no figure at all can't be ranked; a NULL
# currency never matches, so those roles drop out — never impute one. The
# ranking itself lives in rank_top_salaries, not the SQL, so it is unit-testable
# without a database.
TOP_SALARIES_SQL = f"""
SELECT s.raw_posting_id, s.role_index, s.company, s.title, s.location, s.seniority,
       s.salary_min, s.salary_max, s.salary_currency
FROM structured_postings s
JOIN raw_postings r ON r.id = s.raw_posting_id
WHERE (s.salary_max IS NOT NULL OR s.salary_min IS NOT NULL)
  AND s.salary_currency = %(currency)s
  AND {WINDOW_SQL}
"""

# Trend aggregates read full history — no WINDOW_SQL, no db_meta gate. They
# replace the monthly rollup tables dropped in 011: the monthly report's trend
# half buckets straight off the source rows, so every month that ever existed
# is visible, not just the live-agent window.
MONTHLY_SKILL_SQL = """
SELECT date_trunc('month', r.posted_at)::date AS month,
       unnest(s.stack) AS skill,
       count(*) AS roles,
       count(DISTINCT s.raw_posting_id) AS postings
FROM structured_postings s
JOIN raw_postings r ON r.id = s.raw_posting_id
GROUP BY 1, 2
ORDER BY month, roles DESC, skill
"""

MONTHLY_SALARY_SQL = """
SELECT date_trunc('month', r.posted_at)::date AS month,
       s.salary_currency,
       count(*) AS n,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY s.salary_max)
           FILTER (WHERE s.salary_currency IS NOT NULL)::int AS median_max_monthly
FROM structured_postings s
JOIN raw_postings r ON r.id = s.raw_posting_id
WHERE s.salary_max IS NOT NULL
GROUP BY 1, 2
ORDER BY month, n DESC, s.salary_currency
"""


def skill_frequency(conn, limit: int = 20) -> list[SkillCount]:
    with conn.cursor() as cur:
        cur.execute(SKILL_FREQUENCY_SQL, {"limit": limit, "days": WINDOW_DAYS})
        return [SkillCount(*row) for row in cur.fetchall()]


def skill_frequency_by_seniority(conn, seniority: str, limit: int = 20) -> list[SkillCount]:
    with conn.cursor() as cur:
        cur.execute(
            SKILL_FREQUENCY_BY_SENIORITY_SQL,
            {"seniority": seniority, "limit": limit, "days": WINDOW_DAYS},
        )
        return [SkillCount(*row) for row in cur.fetchall()]


def skill_frequency_by_remote(conn, remote: str, limit: int = 20) -> list[SkillCount]:
    with conn.cursor() as cur:
        cur.execute(
            SKILL_FREQUENCY_BY_REMOTE_SQL,
            {"remote": remote, "limit": limit, "days": WINDOW_DAYS},
        )
        return [SkillCount(*row) for row in cur.fetchall()]


def salary_by_currency(conn) -> list[CurrencySalary]:
    with conn.cursor() as cur:
        cur.execute(SALARY_BY_CURRENCY_SQL, {"days": WINDOW_DAYS})
        return [CurrencySalary(*row) for row in cur.fetchall()]


def salary_by_seniority(conn, currency: str = "USD") -> list[SenioritySalary]:
    with conn.cursor() as cur:
        cur.execute(SALARY_BY_SENIORITY_SQL, {"currency": currency, "days": WINDOW_DAYS})
        return [SenioritySalary(*row) for row in cur.fetchall()]


# "Highest salary" is one rule, fixed in code so the model can never pick a
# sort key per question: salary_max, falling back to salary_min when max is
# NULL (a stated floor ranks at its figure, next to ceilings), ties broken by
# the stable key (raw_posting_id, role_index) so reruns are byte-identical.
def rank_top_salaries(rows: list[TopSalary], limit: int = 10) -> list[TopSalary]:
    def key(r: TopSalary) -> tuple[int, int, int]:
        figure = r.salary_max if r.salary_max is not None else r.salary_min
        assert figure is not None  # TOP_SALARIES_SQL filters rows with both NULL
        return (-figure, r.raw_posting_id, r.role_index)

    return sorted(rows, key=key)[:limit]


def top_salaries(conn, currency: str = "USD", limit: int = 10) -> list[TopSalary]:
    with conn.cursor() as cur:
        cur.execute(TOP_SALARIES_SQL, {"currency": currency, "days": WINDOW_DAYS})
        rows = [TopSalary(*row) for row in cur.fetchall()]
    return rank_top_salaries(rows, limit)


def monthly_skill_frequency(conn) -> list[MonthSkill]:
    with conn.cursor() as cur:
        cur.execute(MONTHLY_SKILL_SQL)
        return [MonthSkill(*row) for row in cur.fetchall()]


def monthly_salary(conn) -> list[MonthSalary]:
    with conn.cursor() as cur:
        cur.execute(MONTHLY_SALARY_SQL)
        return [MonthSalary(*row) for row in cur.fetchall()]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analytics over the structured corpus")
    parser.add_argument("--limit", type=int, default=20, help="Top-N skills to show")
    parser.add_argument("--currency", default="USD", help="Currency for the seniority split")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = parse_args()

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        logger.info("=== skill frequency (roles vs postings) ===")
        for s in skill_frequency(conn, args.limit):
            logger.info("%-16s roles=%-4d postings=%d", s.skill, s.roles, s.postings)

        logger.info("=== salary by currency (monthly median of salary_max) ===")
        for c in salary_by_currency(conn):
            logger.info(
                "%-8s n=%-4d median_max_monthly=%s",
                c.currency or "(null)",
                c.n,
                c.median_max_monthly,
            )

        logger.info("=== salary by seniority (%s, monthly median of salary_max) ===", args.currency)
        for r in salary_by_seniority(conn, args.currency):
            logger.info(
                "%-8s roles=%-4d with_salary=%-4d median_max_monthly=%s",
                r.seniority,
                r.roles,
                r.with_salary,
                r.median_max_monthly,
            )


if __name__ == "__main__":
    main()
