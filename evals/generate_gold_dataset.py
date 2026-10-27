import json
import os
import random
from datetime import UTC, datetime
from typing import Any

import psycopg
from dotenv import load_dotenv

load_dotenv()

OUTPUT_PATH = "evals/gold_40_candidates.json"

SEED = 20260813


QUOTAS = {"hn": 16, "habr": 10, "web3": 8, "remotive": 6}


HAND_PICKED_HN = (
    "49262026",  # Stpkr Technologies — "Multiple roles", roles enumerated in body
    "49165353",  # Prior Labs — "Multiple Roles" header (Aug repost of 48757283)
    "49156845",  # Temporal Technologies — "Multiple positions" (Aug repost of 48748961)
    "48755327",  # ViyaMD — two titles in the header line
    "48794583",  # FusionAuth — three titles in the header line
    "48747990",  # We The Flywheel — bulleted "Open roles" in the body
)


EXCLUDED_HN = frozenset(
    {
        # Discussion, self-promotion and spam
        "48749297",  # "don't waste your time with these guys"
        "48749661",  # complaint about advertised salaries
        "48750083",  # "Please normalize 4DWW"
        "48752177",  # link to a job-search tool
        "48754567",  # "I pressed Ctrl-F and search for some programming language names"
        "48755610",  # author promoting their own job search site
        "48852085",  # C++ interview book promotion
        "49224620",  # commentary on building 'boring' infrastructure
        "49159899",  # [flagged]
        "49163435",  # [flagged]
        "49166131",  # bare email address, spam
        # "Who wants to be hired" candidate posts — résumés, not postings. A
        # different document type; extracting a Posting from one is meaningless.
        "48754418",
        "49180607",
        "49184940",
        "49185076",
        "49185343",
    }
)


HN_COMPANY_SQL = "btrim(split_part(split_part(raw_text, '<p>', 1), '|', 1))"

ROW_COLUMNS = "source, external_id, raw_text, posted_at"


def assert_eval(conn: psycopg.Connection) -> None:
    # The mirror of purge.py's 'live' guard. The techanizer_ro role is what actually
    # enforces frozen; this documents the intent and fails before a wasted sample if
    # EVAL_DATABASE_URL has drifted to the live rolling database.
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT role FROM db_meta")
            row = cur.fetchone()
    except psycopg.errors.UndefinedTable as exc:
        raise RuntimeError(
            "db_meta is missing — this database does not declare whether it is live "
            "or an eval snapshot. Point EVAL_DATABASE_URL at the restored snapshot."
        ) from exc

    if row is None:
        raise RuntimeError("db_meta has no row — refusing to sample a database of unknown role.")
    if row[0] != "eval":
        raise RuntimeError(
            f"db_meta.role is {row[0]!r}, not 'eval' — refusing to build a gold set "
            "against the live rolling corpus, whose rows are deleted by the purge."
        )


def fetch_rows(conn: psycopg.Connection, source: str, ids: list[str]) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {ROW_COLUMNS} FROM raw_postings "
            "WHERE source = %s AND external_id = ANY(%s) "
            "ORDER BY external_id",
            (source, ids),
        )
        rows = cur.fetchall()

    found = {row[1] for row in rows}
    missing = sorted(set(ids) - found)
    if missing:
        raise RuntimeError(f"{source}: {len(missing)} id(s) not in the snapshot: {missing}")

    return [
        {
            "source": row[0],
            "external_id": row[1],
            "raw_text": row[2],
            "posted_at": row[3].isoformat() if row[3] else None,
        }
        for row in rows
    ]


def hn_pool(conn: psycopg.Connection, exclude_companies: list[str]) -> list[str]:
    # DISTINCT ON keeps one row per company; ordering by external_id inside the
    # partition makes which one deterministic. Excluding the hand-picked companies
    # (not just their ids) stops a repost of the same company arriving twice.
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT DISTINCT ON ({HN_COMPANY_SQL}) external_id
            FROM raw_postings
            WHERE source = 'hn'
              AND NOT (external_id = ANY(%s))
              AND NOT ({HN_COMPANY_SQL} = ANY(%s))
            ORDER BY {HN_COMPANY_SQL}, external_id
            """,
            (sorted(EXCLUDED_HN), exclude_companies),
        )
        return sorted(row[0] for row in cur.fetchall())


def source_pool(conn: psycopg.Connection, source: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT external_id FROM raw_postings WHERE source = %s ORDER BY external_id",
            (source,),
        )
        return [row[0] for row in cur.fetchall()]


def hand_picked_companies(conn: psycopg.Connection) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT DISTINCT {HN_COMPANY_SQL} FROM raw_postings "
            "WHERE source = 'hn' AND external_id = ANY(%s)",
            (list(HAND_PICKED_HN),),
        )
        return sorted(row[0] for row in cur.fetchall())


def sample(rng: random.Random, pool: list[str], n: int, source: str) -> list[str]:
    if len(pool) < n:
        raise RuntimeError(f"{source}: pool has {len(pool)} rows, need {n}")
    return sorted(rng.sample(pool, n))


def build(conn: psycopg.Connection) -> dict[str, Any]:
    assert_eval(conn)

    # One generator drawn in a fixed source order, so the seed reproduces the whole
    # set. Re-ordering these calls changes the sample even with the seed unchanged.
    rng = random.Random(SEED)

    picked = {"hn": list(HAND_PICKED_HN)}
    picked["hn"] += sample(
        rng,
        hn_pool(conn, hand_picked_companies(conn)),
        QUOTAS["hn"] - len(HAND_PICKED_HN),
        "hn",
    )
    for source in ("habr", "web3", "remotive"):
        picked[source] = sample(rng, source_pool(conn, source), QUOTAS[source], source)

    rows: list[dict[str, Any]] = []
    for source, ids in picked.items():
        rows += fetch_rows(conn, source, ids)

    return {
        "seed": SEED,
        "generated_at": datetime.now(UTC).isoformat(),
        "quotas": QUOTAS,
        "hand_picked_hn": list(HAND_PICKED_HN),
        "excluded_hn": sorted(EXCLUDED_HN),
        "counts": {source: len(ids) for source, ids in picked.items()},
        "rows": rows,
    }


if __name__ == "__main__":
    with psycopg.connect(os.environ["EVAL_DATABASE_URL"]) as conn:
        payload = build(conn)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    counts = ", ".join(f"{source} {n}" for source, n in payload["counts"].items())
    print(f"Wrote {len(payload['rows'])} rows to {OUTPUT_PATH} (seed {SEED}) — {counts}")
