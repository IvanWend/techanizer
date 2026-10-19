import argparse
import asyncio
import logging
import os
from typing import NamedTuple
from uuid import UUID, uuid4

import psycopg
from dotenv import load_dotenv

from jobmarket_agent.extraction.pipeline import (
    Stats as ExtractionStats,
)
from jobmarket_agent.extraction.pipeline import (
    build_agent as build_extractor,
)
from jobmarket_agent.extraction.pipeline import (
    configure_tracing,
)
from jobmarket_agent.extraction.pipeline import (
    pending as pending_extractions,
)
from jobmarket_agent.extraction.pipeline import (
    run as run_extraction,
)
from jobmarket_agent.extraction.prompt import SYSTEM_PROMPT
from jobmarket_agent.ingestion.load import LoadStats, load_all
from jobmarket_agent.storage.embed import (
    Stats as EmbedStats,
)
from jobmarket_agent.storage.embed import (
    pending as pending_embeddings,
)
from jobmarket_agent.storage.embed import (
    run as run_embedding,
)

load_dotenv()

logger = logging.getLogger(__name__)

EXTRACT_MODEL = "deepseek:deepseek-v4-flash"


class WeeklySummary(NamedTuple):
    run_id: UUID
    load: LoadStats
    extract: ExtractionStats
    embed: EmbedStats


# The weekly re-ingestion: load -> extract -> embed, wired as one idempotent
# chain so a timer can call it (see ROADMAP step 7 for the actual cron line).
# Every stage is resumable — each pending() picks up whatever a prior run left —
# so a mid-chain crash re-runs safely. A stage that fails raises and stops the
# run: nothing downstream is useful when the stage before it produced nothing.
async def run_weekly(conn, model: str = EXTRACT_MODEL) -> WeeklySummary:
    run_id = uuid4()
    client = configure_tracing()
    try:
        load_stats = load_all(conn, run_id=run_id)

        rows = pending_extractions(conn)
        agent = build_extractor(model, SYSTEM_PROMPT)
        extract_stats = await run_extraction(conn, agent, rows, model, client=client)

        roles = pending_embeddings(conn)
        embed_stats = run_embedding(conn, roles)

        return WeeklySummary(run_id, load_stats, extract_stats, embed_stats)
    finally:
        if client is not None:
            client.flush()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Weekly ingest: load -> extract -> embed")
    parser.add_argument("--model", default=EXTRACT_MODEL, help="Extraction model")
    parser.add_argument("--dry-run", action="store_true", help="Print pending counts and exit")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = parse_args()

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        if args.dry_run:
            logger.info(
                "dry run: %d rows to extract, %d roles to embed",
                len(pending_extractions(conn)),
                len(pending_embeddings(conn)),
            )
            return

        summary = asyncio.run(run_weekly(conn, model=args.model))
        logger.info(
            "weekly ingest done | run=%s | load ins=%d upd=%d unch=%d fail=%d | "
            "extract ok=%d invalid=%d error=%d roles=%d | embed embedded=%d skipped=%d",
            summary.run_id,
            summary.load.inserted,
            summary.load.updated,
            summary.load.unchanged,
            summary.load.failed,
            summary.extract.ok,
            summary.extract.invalid,
            summary.extract.error,
            summary.extract.roles,
            summary.embed.embedded,
            summary.embed.skipped,
        )


if __name__ == "__main__":
    main()
