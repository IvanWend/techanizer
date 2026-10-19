import argparse
import logging
import os
from collections.abc import Sequence
from typing import NamedTuple

import psycopg
from dotenv import load_dotenv

from jobmarket_agent.ingestion.retention import WINDOW_DAYS
from jobmarket_agent.storage.embed import QUERY_INSTRUCTION, embed, to_vector_text

load_dotenv()

logger = logging.getLogger(__name__)


class SearchHit(NamedTuple):
    raw_posting_id: int
    role_index: int
    title: str | None
    company: str | None
    location: str | None
    seniority: str
    remote_policy: str
    employment_type: str
    stack: list[str]
    salary_min: int | None
    salary_max: int | None
    salary_currency: str | None
    description: str | None
    source: str
    external_id: str
    similarity: float


# The live-agent window (retention.WINDOW_DAYS), applied only when db_meta.role is
# 'live': the frozen eval snapshot serves its whole corpus as current, and its
# role is 'eval'. A scalar subquery keeps the predicate self-contained per query
# rather than a per-call role lookup in Python.
WINDOW_SQL = (
    "((SELECT role FROM db_meta) <> 'live' "
    "OR r.posted_at >= now() - make_interval(days => %(days)s))"
)

# Named params, not positional: the query vector appears twice (projection and
# ORDER BY) and %(qvec)s binds it once. <=> carries the lowest operator
# precedence in Postgres, so the distance expression is always parenthesised.
# Optional filters are `IS NULL OR ... = ANY(%s)`, not dynamic SQL: psycopg3
# adapts list[str] -> text[], and NULL::text[] makes an absent filter a no-op.
SEARCH_SQL = f"""
SELECT s.raw_posting_id, s.role_index, s.title, s.company, s.location,
       s.seniority, s.remote_policy, s.employment_type, s.stack,
       s.salary_min, s.salary_max, s.salary_currency, s.description,
       r.source, r.external_id,
       1 - (e.embedding <=> %(qvec)s::vector) AS similarity
FROM posting_embeddings e
JOIN structured_postings s USING (raw_posting_id, role_index)
JOIN raw_postings      r ON r.id = s.raw_posting_id
WHERE (%(seniority)s::text[] IS NULL OR s.seniority = ANY(%(seniority)s))
  AND (%(remote)s::text[]    IS NULL OR s.remote_policy = ANY(%(remote)s))
  AND {WINDOW_SQL}
ORDER BY e.embedding <=> %(qvec)s::vector
LIMIT %(limit)s
"""


# seniority/remote are Sequence, not list: list is invariant, so the agent
# tool's constrained type (list[Literal[...]]) would not assign to list[str].
# Sequence is covariant and accepts it; the runtime value is always a list,
# which is what psycopg adapts to text[].
def search(
    conn,
    qvec_text: str,
    *,
    limit: int,
    seniority: Sequence[str] | None = None,
    remote: Sequence[str] | None = None,
) -> list[SearchHit]:
    with conn.cursor() as cur:
        cur.execute(
            SEARCH_SQL,
            {
                "qvec": qvec_text,
                "seniority": seniority,
                "remote": remote,
                "limit": limit,
                "days": WINDOW_DAYS,
            },
        )
        return [SearchHit(*row) for row in cur.fetchall()]


def run(
    conn,
    query: str,
    *,
    limit: int = 10,
    seniority: Sequence[str] | None = None,
    remote: Sequence[str] | None = None,
) -> list[SearchHit]:
    vec = embed([QUERY_INSTRUCTION + query])[0]
    return search(conn, to_vector_text(vec), limit=limit, seniority=seniority, remote=remote)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search postings by embedding similarity")
    parser.add_argument("query", help="Natural-language query")
    parser.add_argument("--limit", type=int, default=10, help="Max hits")
    parser.add_argument(
        "--seniority",
        nargs="+",
        choices=["intern", "junior", "mid", "senior", "staff+"],
        default=None,
    )
    parser.add_argument(
        "--remote",
        nargs="+",
        choices=["remote", "hybrid", "onsite"],
        default=None,
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = parse_args()

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        hits = run(
            conn,
            args.query,
            limit=args.limit,
            seniority=args.seniority,
            remote=args.remote,
        )

    if not hits:
        logger.info("No hits.")
        return
    for i, h in enumerate(hits, 1):
        logger.info(
            "[%d] %.3f  %s · %s · %s · %s · %s",
            i,
            h.similarity,
            h.title or "?",
            h.company or "?",
            h.seniority,
            h.remote_policy,
            h.source,
        )


if __name__ == "__main__":
    main()
