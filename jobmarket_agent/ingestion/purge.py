import argparse
import logging

logger = logging.getLogger(__name__)


def purge(conn, *, apply: bool) -> int:
    # Retention is keep-everything now (db/schema/011): the 90-day window is a
    # query-time filter, not a DELETE. Nothing is ever purged.
    logger.info("retention is keep-everything — nothing to purge.")
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(description="No-op: retention is keep-everything.")
    parser.add_argument("--apply", action="store_true", help="ignored — nothing is deleted")
    parser.parse_args()

    logger.info("retention is keep-everything — nothing to purge.")
