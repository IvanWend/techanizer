import argparse
import asyncio
import contextlib
import logging
import os
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, NamedTuple

import psycopg
import psycopg.types.json
from dotenv import load_dotenv
from langfuse import Langfuse, get_client, propagate_attributes
from pydantic_ai import Agent, AgentRunError, UnexpectedModelBehavior

from techanizer.extraction.prompt import SYSTEM_PROMPT
from techanizer.extraction.schema import DocType, NormalizedRole, PostingExtraction
from techanizer.extraction.source_adapters import (
    ExtractionInput,
    apply_ground_truth,
    to_extraction_input,
)
from techanizer.extraction.transform import transform

load_dotenv()

logger = logging.getLogger(__name__)


INSERT_ROLE_SQL = """
INSERT INTO structured_postings (
    raw_posting_id, role_index, company, title, location,
    seniority, remote_policy, employment_type, stack,
    salary_min, salary_max, salary_currency, description, source_quotes,
    derived_fields
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

UPSERT_RUN_SQL = """
INSERT INTO extraction_runs (
    raw_posting_id, status, doc_type, role_count, model, error, extracted_at
) VALUES (%s, %s, %s, %s, %s, %s, now())
ON CONFLICT (raw_posting_id) DO UPDATE
  SET status       = EXCLUDED.status,
      doc_type     = EXCLUDED.doc_type,
      role_count   = EXCLUDED.role_count,
      model        = EXCLUDED.model,
      error        = EXCLUDED.error,
      extracted_at = EXCLUDED.extracted_at
"""


class RawRow(NamedTuple):
    id: int
    source: str
    external_id: str
    raw_text: str


class Stats(NamedTuple):
    ok: int = 0
    invalid: int = 0
    error: int = 0
    roles: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    requests: int = 0


@dataclass(frozen=True)
class Outcome:
    status: Literal["ok", "invalid", "error"]
    model: str
    doc_type: DocType | None = None
    company: str | None = None
    roles: tuple[NormalizedRole, ...] = ()
    error: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    requests: int = 0


def pending(conn, sources=None, limit=None) -> list["RawRow"]:
    query = (
        "SELECT id, source, external_id, raw_text "
        "FROM raw_postings p "
        "WHERE NOT EXISTS ("
        "   SELECT 1 FROM extraction_runs r "
        "   WHERE r.raw_posting_id = p.id"
        ")"
    )
    if sources:
        query += " AND source = ANY(%s)"
    query += " ORDER BY id"
    if limit:
        query += " LIMIT %s"
    with conn.transaction():
        with conn.cursor() as cur:
            args: tuple[Any, ...] = ()
            if sources:
                args += (sources,)
            if limit:
                args += (limit,)

            cur.execute(query, args)
            return [RawRow(*row) for row in cur.fetchall()]


def build_agent(model: str, system_prompt: str, retries=2) -> Agent[None, PostingExtraction]:
    return Agent(
        model=model, system_prompt=system_prompt, output_type=PostingExtraction, retries=retries
    )


@contextlib.contextmanager
def _span(client: Langfuse | None, name: str) -> Iterator[Any]:
    # Every traced block goes through here so an untraced run costs one None check
    # rather than an `if client` around each span.
    if client is None:
        yield None
    else:
        with client.start_as_current_observation(as_type="span", name=name) as span:
            yield span


async def extract(
    agent: Any,
    payload: ExtractionInput,
    model: str,
    raw_posting_id: int | None = None,
    client: Langfuse | None = None,
) -> Outcome:
    with _span(client, "extract-posting") as span:
        outcome = await _extract(agent, payload, model)

        if span is not None:
            span.update(
                input=payload.text,
                output={
                    "company": outcome.company,
                    "roles": [r.model_dump(exclude_none=True) for r in outcome.roles],
                }
                if outcome.status == "ok"
                else None,
                metadata={
                    "raw_posting_id": raw_posting_id,
                    "source": payload.source,
                    "external_id": payload.external_id,
                    "doc_type": outcome.doc_type,
                    "role_count": len(outcome.roles),
                    "status": outcome.status,
                    "prefiltered": outcome.model == "prefilter",
                },
                level="ERROR" if outcome.status != "ok" else None,
                status_message=outcome.error,
            )

        return outcome


async def _extract(agent: Any, payload: ExtractionInput, model: str) -> Outcome:
    if payload.prefilter:
        return Outcome(
            status="ok",
            model="prefilter",
            doc_type=payload.prefilter,
        )
    if payload.text.strip() == "":
        return Outcome(
            status="ok",
            model="prefilter",
            doc_type="other",
        )

    try:
        result = await asyncio.wait_for(
            agent.run(payload.text, model_settings={"timeout": 60}), timeout=65.0
        )
        usage = result.usage
        # Held-out fields never reach the model, so the board's own JSON is the
        # only place they exist. Gaps only — see apply_ground_truth.
        normalized = apply_ground_truth(transform(result.output), payload.ground_truth)

        return Outcome(
            status="ok",
            model=model,
            doc_type=normalized.doc_type,
            company=normalized.company,
            roles=tuple(normalized.roles),
            tokens_in=usage.input_tokens,
            tokens_out=usage.output_tokens,
            requests=usage.requests,
        )

    except UnexpectedModelBehavior as exc:
        return Outcome(status="invalid", model=model, error=str(exc)[:500])

    except (AgentRunError, TimeoutError) as exc:
        return Outcome(status="error", model=model, error=str(exc)[:500])

    except Exception as exc:
        return Outcome(status="error", model=model, error=str(exc)[:500])


def persist(conn, raw_posting_id: int, outcome: Outcome) -> None:
    with conn.transaction(), conn.cursor() as cur:
        if outcome.status == "ok":
            cur.execute(
                "DELETE FROM structured_postings WHERE raw_posting_id = %s", (raw_posting_id,)
            )
            role_rows = [
                (
                    raw_posting_id,
                    role.role_index,
                    outcome.company,
                    role.title,
                    role.location,
                    role.seniority,
                    role.remote_policy,
                    role.employment_type,
                    role.stack,
                    role.salary_min,
                    role.salary_max,
                    role.salary_currency,
                    role.description,
                    psycopg.types.json.Jsonb(role.source_quotes),
                    role.derived_fields,
                )
                for role in outcome.roles
            ]
            if role_rows:
                cur.executemany(INSERT_ROLE_SQL, role_rows)

        cur.execute(
            UPSERT_RUN_SQL,
            (
                raw_posting_id,
                outcome.status,
                outcome.doc_type if outcome.status == "ok" else None,
                len(outcome.roles),
                outcome.model,
                outcome.error,
            ),
        )


async def run(
    conn,
    agent: Agent[None, PostingExtraction],
    rows: list[RawRow],
    model: str,
    chunk_size: int = 5,
    client: Langfuse | None = None,
) -> Stats:

    ok_count = 0
    invalid_count = 0
    error_count = 0
    roles_count = 0
    tokens_in_total = 0
    tokens_out_total = 0
    requests_count = 0

    # One session per invocation: Langfuse then aggregates tokens and cost across
    # every posting of a pass, which `Stats` only ever logs and throws away.
    session = f"extract-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    scope = (
        propagate_attributes(session_id=session, tags=["extraction"])
        if client is not None
        else contextlib.nullcontext()
    )

    with scope:
        for i in range(0, len(rows), chunk_size):
            chunk = rows[i : i + chunk_size]
            logger.info("Processing chunk of %d rows...", len(chunk))

            failed_outcomes = []
            failed_rows = []
            payloads = []

            for row in chunk:
                try:
                    payload = to_extraction_input(row.source, row.external_id, row.raw_text)
                    payloads.append((row.id, payload))
                except Exception as exc:
                    error_outcome = Outcome(status="error", model="adapter", error=str(exc)[:500])
                    failed_outcomes.append(error_outcome)
                    failed_rows.append(row.id)

                    logger.error("Row %s adapter failed: %s", row.id, str(exc)[:200])

                    # Same span name as a model failure: one filter in Langfuse finds
                    # every posting that produced no structured output, whatever the cause.
                    with _span(client, "extract-posting") as span:
                        if span is not None:
                            span.update(
                                input=row.raw_text,
                                metadata={
                                    "raw_posting_id": row.id,
                                    "source": row.source,
                                    "external_id": row.external_id,
                                    "status": "error",
                                    "stage": "adapter",
                                },
                                level="ERROR",
                                status_message=str(exc)[:500],
                            )

            outcomes = await asyncio.gather(
                *(
                    extract(agent, p, model, raw_posting_id=rid, client=client)
                    for rid, p in payloads
                )
            )

            success_idx = 0
            failure_idx = 0

            for row in chunk:
                if row.id in failed_rows:
                    outcome = failed_outcomes[failure_idx]
                    failure_idx += 1
                else:
                    outcome = outcomes[success_idx]
                    success_idx += 1

                persist(conn, row.id, outcome)

                if outcome.status == "ok":
                    ok_count += 1
                elif outcome.status == "invalid":
                    invalid_count += 1
                elif outcome.status == "error":
                    error_count += 1

                roles_count += len(outcome.roles)
                tokens_in_total += outcome.tokens_in
                tokens_out_total += outcome.tokens_out
                requests_count += outcome.requests

            done = i + len(chunk)
            logger.info(
                "[%d/%d] Progress | ok=%d invalid=%d error=%d roles=%d in=%d out=%d req=%d",
                done,
                len(rows),
                ok_count,
                invalid_count,
                error_count,
                roles_count,
                tokens_in_total,
                tokens_out_total,
                requests_count,
            )

    return Stats(
        ok=ok_count,
        invalid=invalid_count,
        error=error_count,
        roles=roles_count,
        tokens_in=tokens_in_total,
        tokens_out=tokens_out_total,
        requests=requests_count,
    )


def parse_pipeline_args():
    parser = argparse.ArgumentParser(description="Pipeline entrypoint")

    parser.add_argument(
        "--source",
        action="append",
        default=None,
        help="Repeatable source filter",
    )

    parser.add_argument("--limit", type=int, default=None, help="Max pending row to process")

    parser.add_argument(
        "--chunk-size", type=int, default=20, help="Default number of chunks to process"
    )

    parser.add_argument(
        "--model",
        type=str,
        default="deepseek:deepseek-v4-flash",
        help="Default name of the LLM model",
    )

    parser.add_argument("--dry-run", action="store_true", help="Dry run action")

    return parser.parse_args()


def configure_tracing() -> Langfuse | None:
    """Return an authenticated Langfuse client, or None so the pipeline runs untraced."""
    if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
        logger.info("Langfuse keys absent — running untraced.")
        return None

    client = get_client()
    if not client.auth_check():
        logger.warning("Langfuse auth check failed — running untraced.")
        return None

    # Emits the agent-run and model-request spans. The Langfuse client owns the
    # global tracer provider they export through, so this needs no arguments.
    Agent.instrument_all()
    logger.info("Langfuse tracing enabled (%s).", os.getenv("LANGFUSE_BASE_URL", "default host"))
    return client


async def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    args = parse_pipeline_args()

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        rows = pending(conn, sources=args.source, limit=args.limit)
        logger.info("Found %d pending rows to process.", len(rows))

        if args.dry_run:
            logger.info("Dry run mode enabled. No changes will be persisted.")
            source_counts = Counter(row.source for row in rows)
            logger.info("Pending row breakdown by source: %s", dict(source_counts))
            first_few_ids = [row.id for row in rows[:5]]
            logger.info("First few pending row IDs: %s", first_few_ids)
            return

        client = configure_tracing()

        try:
            agent = build_agent(args.model, SYSTEM_PROMPT)
            t0 = time.perf_counter()
            stats = await run(conn, agent, rows, args.model, args.chunk_size, client=client)
            elapsed = time.perf_counter() - t0
            logger.info(
                "Extraction completed in %.2f seconds | "
                "ok=%d invalid=%d error=%d roles=%d in=%d out=%d req=%d",
                elapsed,
                stats.ok,
                stats.invalid,
                stats.error,
                stats.roles,
                stats.tokens_in,
                stats.tokens_out,
                stats.requests,
            )
        finally:
            # Spans are batched; a short run exits before the flush timer fires.
            if client is not None:
                client.flush()


if __name__ == "__main__":
    asyncio.run(main())
