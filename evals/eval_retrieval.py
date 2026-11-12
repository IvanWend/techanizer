import argparse
import os

import psycopg
from dotenv import load_dotenv

from techanizer.ingestion.retention import WINDOW_DAYS
from techanizer.storage.vector_search import run as vector_search_run

load_dotenv()

# Lexical ground truth: a role is "relevant" if its normalized `stack` contains
# the skill (or its `title` contains the substring). Semantic search should at
# least surface the roles that literally state the term, so a miss here is a
# real retrieval failure, not a scoring artifact.
QUERIES = [
    ("golang backend engineer", ("skill", "go")),
    ("rust systems programmer", ("skill", "rust")),
    ("kubernetes platform engineer", ("skill", "kubernetes")),
    ("terraform infrastructure engineer", ("skill", "terraform")),
    ("solidity smart contract developer", ("skill", "solidity")),
    ("clickhouse analytics database", ("skill", "clickhouse")),
    ("pytorch deep learning", ("skill", "pytorch")),
    ("kafka event streaming", ("skill", "kafka")),
    ("postgresql database engineer", ("skill", "postgres")),
    ("react frontend developer", ("skill", "react")),
    ("data engineer", ("title", "%data engineer%")),
    ("devops engineer", ("title", "%devops%")),
]

# Mirrors vector_search.WINDOW_SQL: role='eval' serves the whole corpus.
WINDOW_SQL = (
    "((SELECT role FROM db_meta) <> 'live' OR r.posted_at >= now() - make_interval(days => %s))"
)


def ground_truth(conn, kind: str, term: str) -> set[tuple[int, int]]:
    pred = "%s = ANY(s.stack)" if kind == "skill" else "s.title ILIKE %s"
    sql = f"""
    SELECT s.raw_posting_id, s.role_index
    FROM structured_postings s
    JOIN raw_postings r ON r.id = s.raw_posting_id
    JOIN posting_embeddings e
      ON e.raw_posting_id = s.raw_posting_id AND e.role_index = s.role_index
    WHERE {pred}
      AND {WINDOW_SQL}
    """
    with conn.cursor() as cur:
        cur.execute(sql, (term, WINDOW_DAYS))
        return {(rid, ridx) for rid, ridx in cur.fetchall()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20, help="Top-k retrieved per query")
    parser.add_argument("--verbose", action="store_true", help="Show top-3 hits per query")
    args = parser.parse_args()

    db_url = os.environ.get("EVAL_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not db_url:
        raise ValueError("set EVAL_DATABASE_URL or DATABASE_URL")

    with psycopg.connect(db_url) as conn:
        computed = []  # (text, n, hit10, p10, r10, r20, mrr, hits, relevant)
        for text, (kind, term) in QUERIES:
            relevant = ground_truth(conn, kind, term)
            hits = vector_search_run(conn, text, limit=args.limit)
            ranked = [(h.raw_posting_id, h.role_index) for h in hits]

            top10 = ranked[:10]
            inter10 = [x for x in top10 if x in relevant]
            n = len(relevant)
            hit10 = 1 if inter10 else 0
            p10 = len(inter10) / 10
            r10 = len(inter10) / n if n else 0.0
            r20 = sum(1 for x in ranked[:20] if x in relevant) / n if n else 0.0
            mrr = 0.0
            for i, x in enumerate(ranked, start=1):
                if x in relevant:
                    mrr = 1.0 / i
                    break
            computed.append((text, n, hit10, p10, r10, r20, mrr, hits, relevant))

        if computed:
            mean_p = sum(c[3] for c in computed) / len(computed)
            mean_mrr = sum(c[6] for c in computed) / len(computed)
            print(
                f"retrieval: MRR {mean_mrr:.2f}  precision@10 {mean_p:.2f}  "
                f"({len(computed)} queries, top-{args.limit})"
            )
        print(f"{'query':38} {'N':>4} {'hit@10':>6} {'P@10':>5} {'R@10':>5} {'R@20':>5} {'MRR':>6}")
        print("-" * 74)
        for text, n, hit10, p10, r10, r20, mrr, hits, relevant in computed:
            print(f"{text:38} {n:>4} {hit10:>6d} {p10:>5.2f} {r10:>5.2f} {r20:>5.2f} {mrr:>6.3f}")
            if args.verbose:
                for h in hits[:3]:
                    tag = "•" if (h.raw_posting_id, h.role_index) in relevant else " "
                    print(f"    {tag} {h.similarity:.3f}  {h.title or '?'} @ {h.company or '?'}")
                print()


if __name__ == "__main__":
    main()
