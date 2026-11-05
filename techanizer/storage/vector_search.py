import argparse
import logging
import os
import re
from collections.abc import Sequence
from typing import NamedTuple

import psycopg
from dotenv import load_dotenv

from techanizer.ingestion.retention import WINDOW_DAYS
from techanizer.storage.embed import QUERY_INSTRUCTION, embed, to_vector_text

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

# Bare tech tokens with strong non-technical senses (kafka, solidity) embed
# toward the wrong meaning, and "postgres" ranks weakly on its own. Appending a
# disambiguating suffix anchors the query to the intended sense.
QUERY_EXPANSION = {
    "kafka": "stream processing",
    "postgres": "relational database",
    "postgresql": "relational database",
    "solidity": "ethereum blockchain",
}


def expand_query(query: str) -> str:
    out = query
    for token, suffix in QUERY_EXPANSION.items():
        if re.search(rf"\b{re.escape(token)}\b", out, re.IGNORECASE) and suffix not in out:
            out = f"{out} {suffix}"
    return out


def lexical_terms(query: str) -> list[str]:
    """The query's tokens, lowercased and split on non-word chars (mirroring the
    'simple' parser), for exact stack-element matching."""
    return [t for t in re.split(r"\W+", query.lower()) if t]


def lexical_tsquery(query: str) -> str:
    """OR the query's words for the exact-token leg.

    plainto_tsquery and websearch_to_tsquery AND unquoted words, so a role whose
    stack says only "kafka" would not match "kafka event streaming". OR-ing the
    words (split on non-word chars, mirroring the 'simple' parser) lets any one
    token match; ts_rank then prefers roles matching more of them.
    """
    return " | ".join(lexical_terms(query))


# Reciprocal-rank fusion tuning: k is the rank-dampening constant, and each leg
# fetches a candidate pool larger than `limit` so fusion can re-rank. The
# lexical leg matches the query's surface tokens, so it can rescue a role the
# embedding mis-encodes (kafka → the novelist) without the alias map.
FUSION_K = 60
CANDIDATE_MULTIPLIER = 4
MIN_CANDIDATES = 100


# The two legs share the hit projection (and its dense similarity) plus the
# seniority/remote/window filters; they differ only in the match predicate and
# the ORDER BY. `similarity` stays the dense cosine score on both legs — the
# fused RRF score only drives the final ordering, never the reported number.
_HIT_COLUMNS = """
SELECT s.raw_posting_id, s.role_index, s.title, s.company, s.location,
       s.seniority, s.remote_policy, s.employment_type, s.stack,
       s.salary_min, s.salary_max, s.salary_currency, s.description,
       r.source, r.external_id,
       1 - (e.embedding <=> %(qvec)s::vector) AS similarity
FROM posting_embeddings e
JOIN structured_postings s USING (raw_posting_id, role_index)
JOIN raw_postings      r ON r.id = s.raw_posting_id
"""

# Named params, not positional: the query vector appears twice (projection and
# ORDER BY) and %(qvec)s binds it once. <=> carries the lowest operator
# precedence in Postgres, so the distance expression is always parenthesised.
# Optional filters are `IS NULL OR ... = ANY(%s)`, not dynamic SQL: psycopg3
# adapts list[str] -> text[], and NULL::text[] makes an absent filter a no-op.
_FILTERS = f"""
WHERE (%(seniority)s::text[] IS NULL OR s.seniority = ANY(%(seniority)s))
  AND (%(remote)s::text[]    IS NULL OR s.remote_policy = ANY(%(remote)s))
  AND {WINDOW_SQL}
"""

DENSE_SQL = (
    _HIT_COLUMNS
    + _FILTERS
    + """
ORDER BY e.embedding <=> %(qvec)s::vector
LIMIT %(limit)s
"""
)

# websearch_to_tsquery and plainto_tsquery AND the query's words, which would
# drop a role whose stack has only "kafka" when asked "kafka event streaming".
# lexical_tsquery ORs them instead, so any single token matches; ts_rank then
# orders multi-term matches first. 'simple' matches the column's config
# (cross-language exact-token, no stemming, no stopwords).
#
# An exact stack element match (sk = ANY(terms)) ranks above a token match,
# because the 'simple' parser splits hyphens: "kafka-streams" tokenizes to
# "kafka" + "streams", which would let a kafka-streams role masquerade as a
# "kafka" one. The eval's ground truth is `term = ANY(stack)`, so the lexical
# leg's ordering mirrors it.
LEXICAL_SQL = (
    _HIT_COLUMNS
    + _FILTERS
    + """
  AND (s.search_tsv @@ to_tsquery('simple', %(tsq)s)
       OR EXISTS (SELECT 1 FROM unnest(s.stack) sk WHERE sk = ANY(%(terms)s)))
ORDER BY (EXISTS (SELECT 1 FROM unnest(s.stack) sk WHERE sk = ANY(%(terms)s))) DESC,
         ts_rank(s.search_tsv, to_tsquery('simple', %(tsq)s)) DESC
LIMIT %(limit)s
"""
)


# seniority/remote are Sequence, not list: list is invariant, so the agent
# tool's constrained type (list[Literal[...]]) would not assign to list[str].
# Sequence is covariant and accepts it; the runtime value is always a list,
# which is what psycopg adapts to text[].
def reciprocal_rank_fusion(
    dense: list[SearchHit],
    lexical: list[SearchHit],
    *,
    k: int = FUSION_K,
    limit: int = 10,
) -> list[SearchHit]:
    scores: dict[tuple[int, int], float] = {}
    dense_rank: dict[tuple[int, int], int] = {}
    hits: dict[tuple[int, int], SearchHit] = {}

    for rank, hit in enumerate(dense, start=1):
        key = (hit.raw_posting_id, hit.role_index)
        scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
        dense_rank[key] = rank
        hits[key] = hit

    for rank, hit in enumerate(lexical, start=1):
        key = (hit.raw_posting_id, hit.role_index)
        scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
        if key not in dense_rank:
            # Lexical-only docs rank after every dense doc at equal fused score,
            # so the dense similarity stays the tie-break for the reported number.
            dense_rank[key] = len(dense) + 1
            hits[key] = hit

    def sort_key(key: tuple[int, int]) -> tuple[float, int, int, int]:
        return (-scores[key], dense_rank[key], key[0], key[1])

    return [hits[key] for key in sorted(scores, key=sort_key)[:limit]]


def search(
    conn,
    qvec_text: str,
    query: str,
    *,
    limit: int,
    seniority: Sequence[str] | None = None,
    remote: Sequence[str] | None = None,
) -> list[SearchHit]:
    candidate = max(limit * CANDIDATE_MULTIPLIER, MIN_CANDIDATES)
    params = {
        "qvec": qvec_text,
        "tsq": lexical_tsquery(query),
        "terms": lexical_terms(query),
        "seniority": seniority,
        "remote": remote,
        "limit": candidate,
        "days": WINDOW_DAYS,
    }
    with conn.cursor() as cur:
        cur.execute(DENSE_SQL, params)
        dense = [SearchHit(*row) for row in cur.fetchall()]
        cur.execute(LEXICAL_SQL, params)
        lexical = [SearchHit(*row) for row in cur.fetchall()]
    return reciprocal_rank_fusion(dense, lexical, limit=limit)


def run(
    conn,
    query: str,
    *,
    limit: int = 10,
    seniority: Sequence[str] | None = None,
    remote: Sequence[str] | None = None,
) -> list[SearchHit]:
    vec = embed([QUERY_INSTRUCTION + expand_query(query)])[0]
    return search(
        conn,
        to_vector_text(vec),
        query,
        limit=limit,
        seniority=seniority,
        remote=remote,
    )


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
