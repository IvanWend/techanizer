import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import NamedTuple
from uuid import UUID

import psycopg
from dotenv import load_dotenv

from jobmarket_agent.ingestion.habr_client import HABR_DELAY_S, HABR_LIST_URL
from jobmarket_agent.ingestion.habr_client import fetch_description_html as fetch_habr_description
from jobmarket_agent.ingestion.habr_client import fetch_vacancy_cards as fetch_habr_cards
from jobmarket_agent.ingestion.habr_client import new_session as new_habr_session
from jobmarket_agent.ingestion.hn_client import HNThread, fetch_thread, find_hiring_threads
from jobmarket_agent.ingestion.remotive_client import REMOTIVE_ENDPOINT_URL
from jobmarket_agent.ingestion.remotive_client import fetch_latest_jobs as fetch_remotive_jobs
from jobmarket_agent.ingestion.retention import WINDOW_DAYS, window_cutoff
from jobmarket_agent.ingestion.web3_client import WEB3_ENDPOINT_URL
from jobmarket_agent.ingestion.web3_client import fetch_latest_jobs as fetch_web3_jobs
from jobmarket_agent.ops.run_log import begin_run, end_run

load_dotenv()

logger = logging.getLogger(__name__)

UPSERT_SQL = """
INSERT INTO raw_postings (source, external_id, raw_text, thread_month, posted_at)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (source, external_id) DO UPDATE
  SET raw_text   = EXCLUDED.raw_text,
      posted_at  = EXCLUDED.posted_at,
      updated_at = now()
  WHERE raw_postings.raw_text IS DISTINCT FROM EXCLUDED.raw_text
RETURNING (xmax = 0) AS inserted;
"""

KNOWN_IDS_SQL = "SELECT external_id FROM raw_postings WHERE source = %s"

TOUCH_LAST_SEEN_SQL = """
UPDATE raw_postings SET last_seen_at = now()
 WHERE source = %s AND external_id = ANY(%s)
"""

# Habr's detail fetches are the one part of a run that takes minutes rather than
# seconds, so it heartbeats instead of going silent. Per-posting logging would be
# ~460 lines for a job that runs unattended.
HABR_PROGRESS_EVERY = 50


# A tuple so psycopg can pass it straight to UPSERT_SQL as parameters; named so
# the pipeline can read row.posted_at instead of row[4]. Field order is the
# INSERT column order and must stay that way.
class Row(NamedTuple):
    source: str
    external_id: str
    raw_text: str
    thread_month: date | None
    posted_at: datetime


@dataclass(frozen=True)
class LoadStats:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: int = 0

    def __add__(self, other: "LoadStats") -> "LoadStats":
        return LoadStats(
            self.inserted + other.inserted,
            self.updated + other.updated,
            self.unchanged + other.unchanged,
            self.failed + other.failed,
        )


def to_posted_at(created_at: str) -> datetime:
    dt = datetime.fromisoformat(created_at)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def to_thread_month(posted_at: datetime) -> date:
    return posted_at.date().replace(day=1)


def hnthread_to_rows(thread: HNThread) -> list[Row]:
    thread_month = to_thread_month(to_posted_at(thread.created_at))
    rows: list[Row] = []
    for comment in thread.comments:
        posted_at = to_posted_at(comment.created_at)
        rows.append(Row("hn", comment.id, comment.text, thread_month, posted_at))
    return rows


def remotive_to_rows(jobs: list[dict]) -> list[Row]:
    rows: list[Row] = []
    for job in jobs:
        posted_at = to_posted_at(job["publication_date"])
        thread_month = to_thread_month(posted_at)
        rows.append(Row("remotive", str(job["id"]), json.dumps(job), thread_month, posted_at))
    return rows


def web3_to_rows(jobs: list[dict]) -> list[Row]:
    rows: list[Row] = []
    for job in jobs:
        # `date` is RFC 2822 ("Mon, 10 Aug 2026 22:04:14 +0100"), which
        # fromisoformat cannot read; date_epoch carries the same instant.
        posted_at = datetime.fromtimestamp(int(job["date_epoch"]), tz=UTC)
        thread_month = to_thread_month(posted_at)
        rows.append(Row("web3", str(job["id"]), json.dumps(job), thread_month, posted_at))
    return rows


def habr_to_rows(cards: list[dict]) -> list[Row]:
    rows: list[Row] = []
    for card in cards:
        posted_at = to_posted_at(card["publishedDate"]["date"])
        thread_month = to_thread_month(posted_at)
        # ensure_ascii=False keeps the Cyrillic readable in the column instead of
        # storing it as \uXXXX escapes at roughly six bytes per character.
        raw_text = json.dumps(card, ensure_ascii=False)
        rows.append(Row("habr", str(card["id"]), raw_text, thread_month, posted_at))
    return rows


def fetch_hn_rows(known_ids: set[str]) -> list[Row]:
    # A monthly thread's comments span the month after its creation, so select
    # threads with a one-month margin beyond the window; within_window() in
    # load_source trims individual comments to the exact cutoff.
    margin_cutoff = window_cutoff() - timedelta(days=31)
    rows: list[Row] = []
    for story_id, _title, created_at in find_hiring_threads():
        if to_posted_at(created_at) < margin_cutoff:
            continue
        rows.extend(hnthread_to_rows(fetch_thread(story_id)))
    return rows


def fetch_remotive_rows(known_ids: set[str]) -> list[Row]:
    return remotive_to_rows(fetch_remotive_jobs(REMOTIVE_ENDPOINT_URL))


def fetch_web3_rows(known_ids: set[str]) -> list[Row]:
    return web3_to_rows(fetch_web3_jobs(WEB3_ENDPOINT_URL, os.getenv("WEB3_API_KEY")))


def fetch_habr_rows(known_ids: set[str]) -> list[Row]:
    session = new_habr_session()
    cards = fetch_habr_cards(HABR_LIST_URL, session=session)
    cutoff = window_cutoff()

    # Habr is the only source whose prose costs one HTTP request per posting, so
    # every filter runs *before* the detail fetch rather than in within_window()
    # afterwards — otherwise every run pays ~460 requests to rebuild rows it
    # already has. The tradeoff: a posting already stored is never re-fetched, so
    # an edited Habr posting keeps its original text and the guarded upsert's
    # `updated` path stays dead for this source.
    seen: set[str] = set()
    fresh: list[dict] = []
    duplicates = 0
    for card in cards:
        external_id = str(card["id"])
        # The list endpoint pages by offset over a live, date-desc feed, so a
        # posting published mid-run shifts everything down and a card on a page
        # boundary comes back twice. Dropping it here saves the second detail
        # fetch and keeps `unchanged` from counting a row that never existed.
        # The flip side of the same shift — a card skipped entirely — is not
        # fixable here, but it lands on the next run as a new id.
        if external_id in seen:
            duplicates += 1
            continue
        if external_id in known_ids:
            continue
        if to_posted_at(card["publishedDate"]["date"]) < cutoff:
            continue
        seen.add(external_id)
        fresh.append(card)

    logger.info(
        "habr: %d cards, %d new and in-window, %d stored or aged out, %d paging duplicates.",
        len(cards),
        len(fresh),
        len(cards) - len(fresh) - duplicates,
        duplicates,
    )

    enriched: list[dict] = []
    missing = 0
    for index, card in enumerate(fresh):
        # The client throttles its own list pages; spacing out the detail fetches
        # is the caller's job, and this is the only caller that makes ~460 of them.
        if index:
            time.sleep(HABR_DELAY_S)
        if index % HABR_PROGRESS_EVERY == 0:
            logger.info("habr: fetching descriptions %d/%d…", index + 1, len(fresh))

        description_html = fetch_habr_description(str(card["id"]), session=session)
        if description_html is None:
            missing += 1
        enriched.append(card | {"description_html": description_html})

    # A description comes back None for an archived posting, but also if the page
    # markup moved out from under DESCRIPTION_SELECTOR — in which case every row
    # still inserts cleanly, just with no prose to extract from. Loud on the way
    # past, so a silently gutted source cannot look like a healthy run.
    if missing:
        log = logger.warning if missing > len(fresh) // 10 else logger.info
        log("habr: %d/%d postings returned no description.", missing, len(fresh))

    return habr_to_rows(enriched)


# One entry per source. Each fetcher owns exactly one board, so a board that is
# down, rate-limited or has changed shape costs its own rows and nothing else.
# Every fetcher is handed the external_ids already stored for its source; only
# habr uses them, because only habr pays per-posting for the text it would refetch.
SOURCES: dict[str, Callable[[set[str]], list[Row]]] = {
    "hn": fetch_hn_rows,
    "remotive": fetch_remotive_rows,
    "web3": fetch_web3_rows,
    "habr": fetch_habr_rows,
}


def within_window(rows: list[Row]) -> list[Row]:
    cutoff = window_cutoff()
    return [row for row in rows if row.posted_at >= cutoff]


def upsert_posting(conn, source: str, rows) -> LoadStats:
    inserted = updated = failed = 0
    # The outer block is the real transaction, so the source lands atomically
    # and conn.transaction() inside the loop opens a savepoint rather than
    # committing every row on its own.
    with conn.transaction(), conn.cursor() as cur:
        for row in rows:
            try:
                # A failed statement aborts the whole transaction in psycopg;
                # the savepoint keeps one bad row from taking the rest with it.
                with conn.transaction():
                    cur.execute(UPSERT_SQL, row)
                    result = cur.fetchone()
            except psycopg.Error:
                logger.exception("upsert failed for %s/%s", row.source, row.external_id)
                failed += 1
                continue
            if result is None:
                continue
            elif result[0]:
                inserted += 1
            else:
                updated += 1

        # last_seen_at records "the board still served this id to us" and must
        # advance every run, not only when raw_text changed (a byte-identical
        # posting is still live). The guarded upsert above skips unchanged rows,
        # so the touch is a separate statement over the same row set; a row that
        # failed to insert matches nothing and is skipped for free.
        if rows:
            cur.execute(TOUCH_LAST_SEEN_SQL, (source, [row.external_id for row in rows]))

    unchanged = len(rows) - inserted - updated - failed
    return LoadStats(inserted, updated, unchanged, failed)


def known_external_ids(conn, source: str) -> set[str]:
    # Wrapped in an explicit transaction block so the connection is back to idle
    # before the caller starts its network I/O. psycopg opens a transaction on
    # the first statement and leaves it open: a multi-minute fetch would sit
    # idle-in-transaction, and worse, upsert_posting's conn.transaction() would
    # then nest as a savepoint instead of being the real per-source transaction
    # it is written to be.
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(KNOWN_IDS_SQL, (source,))
        return {row[0] for row in cur}


def load_source(
    conn, name: str, fetch: Callable[[set[str]], list[Row]], run_id: UUID | None = None
) -> LoadStats:
    # run-log bookkeeping brackets the fetch: begin before any network I/O, end
    # (with counts) after the upsert, end (with error) if anything throws. The
    # helpers commit their own transactions, so the error row survives the
    # rollback load_all performs on a failed source.
    if run_id is not None:
        begin_run(conn, run_id, name)
    try:
        rows = fetch(known_external_ids(conn, name))
        fresh = within_window(rows)
        logger.info(
            "%s: fetched %d postings, %d within %d days.", name, len(rows), len(fresh), WINDOW_DAYS
        )

        stats = upsert_posting(conn, name, fresh)
        logger.info(
            "%s: inserted %d, updated %d, unchanged %d, failed %d.",
            name,
            stats.inserted,
            stats.updated,
            stats.unchanged,
            stats.failed,
        )
        if run_id is not None:
            end_run(conn, run_id, name, len(rows), stats)
        return stats
    except Exception as exc:
        if run_id is not None:
            end_run(conn, run_id, name, None, error=str(exc)[:500])
        raise


def load_all(conn, run_id: UUID | None = None) -> LoadStats:
    total = LoadStats()
    succeeded = 0
    for name, fetch in SOURCES.items():
        try:
            total += load_source(conn, name, fetch, run_id=run_id)
            succeeded += 1
        except Exception:
            logger.exception("%s: source failed, continuing with the rest", name)
            conn.rollback()

    if succeeded == 0:
        raise RuntimeError(f"all {len(SOURCES)} sources failed — see the logged tracebacks")
    return total


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        stats = load_all(conn)
        logger.info(
            "Total — inserted: %d, updated: %d, unchanged: %d, failed: %d",
            stats.inserted,
            stats.updated,
            stats.unchanged,
            stats.failed,
        )
