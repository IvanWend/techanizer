"""Repairs rows extracted before the salary-quote fallback and the ground-truth
fill existed, without calling the model again.

Both passes are gap-fills over data already on disk — the model's own salary
quote, and the board JSON in raw_postings — so re-running is a no-op and a run
that dies part-way leaves a consistent corpus behind."""

import argparse
import logging
import os
from collections.abc import Iterator
from typing import Any, NamedTuple

import psycopg
from dotenv import load_dotenv

from techanizer.extraction.schema import NormalizedPosting, NormalizedRole
from techanizer.extraction.source_adapters import (
    GroundTruth,
    apply_ground_truth,
    to_extraction_input,
)
from techanizer.extraction.transform import resolve_salary

load_dotenv()

logger = logging.getLogger(__name__)

SELECT_SQL = """
SELECT s.raw_posting_id, r.source, r.external_id, r.raw_text, s.company,
       s.role_index, s.title, s.location, s.seniority, s.remote_policy,
       s.employment_type, s.stack, s.salary_min, s.salary_max, s.salary_currency,
       s.description, s.source_quotes, s.derived_fields
FROM structured_postings s
JOIN raw_postings r ON r.id = s.raw_posting_id
{where}
ORDER BY s.raw_posting_id, s.role_index
"""

UPDATE_SQL = """
UPDATE structured_postings SET
    company         = %s,
    title           = %s,
    location        = %s,
    seniority       = %s,
    remote_policy   = %s,
    employment_type = %s,
    stack           = %s,
    salary_min      = %s,
    salary_max      = %s,
    salary_currency = %s,
    derived_fields  = %s,
    updated_at      = now()
WHERE raw_posting_id = %s AND role_index = %s
"""

_SALARY_FIELDS = ("salary_min", "salary_max", "salary_currency")


class Repaired(NamedTuple):
    postings: int = 0
    roles: int = 0
    salary_filled: int = 0
    salary_repaired: int = 0
    salary_dropped: int = 0
    ground_truth_filled: int = 0
    adapter_failed: int = 0


def _rows(conn, sources: list[str] | None) -> Iterator[tuple[int, str, str, str, list[Any]]]:
    """One group per posting: apply_ground_truth fills `company` posting-wide, so
    the roles of a posting have to be decided together."""
    where = "WHERE r.source = ANY(%s)" if sources else ""
    args = (sources,) if sources else ()
    with conn.cursor() as cur:
        cur.execute(SELECT_SQL.format(where=where), args)

        current: tuple[int, str, str, str] | None = None
        roles: list[Any] = []
        for row in cur:
            head, tail = tuple(row[:5]), list(row[5:])
            if current is not None and head[0] != current[0]:
                yield (*current, roles)
                roles = []
            current = (head[0], head[1], head[2], head[3])
            company = head[4]
            roles.append([company, *tail])
        if current is not None:
            yield (*current, roles)


def _to_posting(company: str | None, rows: list[Any]) -> NormalizedPosting:
    roles = [
        NormalizedRole(
            role_index=row[1],
            title=row[2],
            location=row[3],
            seniority=row[4],
            remote_policy=row[5],
            employment_type=row[6],
            stack=row[7],
            salary_min=row[8],
            salary_max=row[9],
            salary_currency=row[10],
            description=row[11],
            source_quotes=row[12] or {},
            derived_fields=row[13] or [],
        )
        for row in rows
    ]
    return NormalizedPosting(doc_type="posting", company=company, roles=roles)


def repair_salary(posting: NormalizedPosting) -> tuple[NormalizedPosting, Repaired]:
    filled = repaired = dropped = 0
    roles = []
    for role in posting.roles:
        band = resolve_salary(
            role.salary_min, role.salary_max, role.salary_currency, role.source_quotes
        )
        if band == (role.salary_min, role.salary_max, role.salary_currency):
            roles.append(role)
            continue

        if role.salary_min is None and role.salary_max is None:
            filled += 1
        elif band == (None, None, None):
            dropped += 1
        else:
            repaired += 1

        update: dict[str, Any] = dict(zip(_SALARY_FIELDS, band, strict=True))
        update["derived_fields"] = sorted({*role.derived_fields, *_SALARY_FIELDS})
        roles.append(role.model_copy(update=update))

    stats = Repaired(salary_filled=filled, salary_repaired=repaired, salary_dropped=dropped)
    return posting.model_copy(update={"roles": roles}), stats


def repair(posting: NormalizedPosting, truth: GroundTruth) -> tuple[NormalizedPosting, Repaired]:
    after_salary, stats = repair_salary(posting)
    after = apply_ground_truth(after_salary, truth)
    gt_filled = sum(
        1
        for before, role in zip(after_salary.roles, after.roles, strict=True)
        if role.derived_fields != before.derived_fields
    )
    return after, stats._replace(ground_truth_filled=gt_filled)


def run(conn, sources: list[str] | None = None, dry_run: bool = False) -> Repaired:
    totals = Repaired()
    failed = 0

    for raw_posting_id, source, external_id, raw_text, rows in _rows(conn, sources):
        before = _to_posting(rows[0][0], rows)
        try:
            truth = to_extraction_input(source, external_id, raw_text).ground_truth
        except Exception as exc:
            logger.error("Row %s adapter failed: %s", raw_posting_id, str(exc)[:200])
            failed += 1
            continue

        after, stats = repair(before, truth)
        changed = [
            (b, a)
            for b, a in zip(before.roles, after.roles, strict=True)
            if a != b or after.company != before.company
        ]
        if not changed:
            continue

        summed = [x + y for x, y in zip(totals, stats, strict=True)]
        totals = Repaired(*summed)._replace(
            postings=totals.postings + 1, roles=totals.roles + len(changed)
        )

        if dry_run:
            for _, role in changed:
                logger.info(
                    "%s/%s %s | %s %s-%s %s | derived=%s",
                    raw_posting_id,
                    role.role_index,
                    source,
                    after.company,
                    role.salary_min,
                    role.salary_max,
                    role.salary_currency,
                    role.derived_fields,
                )
            continue

        # One transaction per posting, matching persist(): a crash leaves whole
        # postings done or untouched, never half a posting's roles.
        with conn.transaction(), conn.cursor() as cur:
            cur.executemany(
                UPDATE_SQL,
                [
                    (
                        after.company,
                        role.title,
                        role.location,
                        role.seniority,
                        role.remote_policy,
                        role.employment_type,
                        role.stack,
                        role.salary_min,
                        role.salary_max,
                        role.salary_currency,
                        role.derived_fields,
                        raw_posting_id,
                        role.role_index,
                    )
                    for _, role in changed
                ],
            )

    return totals._replace(adapter_failed=failed)


def parse_backfill_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", default=None, help="Repeatable source filter")
    parser.add_argument("--dry-run", action="store_true", help="Log the changes, write nothing")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = parse_backfill_args()

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        totals = run(conn, sources=args.source, dry_run=args.dry_run)

    logger.info(
        "Backfill %s | postings=%d roles=%d salary(filled=%d repaired=%d dropped=%d) "
        "ground_truth=%d adapter_failed=%d",
        "dry run" if args.dry_run else "complete",
        totals.postings,
        totals.roles,
        totals.salary_filled,
        totals.salary_repaired,
        totals.salary_dropped,
        totals.ground_truth_filled,
        totals.adapter_failed,
    )


if __name__ == "__main__":
    main()
