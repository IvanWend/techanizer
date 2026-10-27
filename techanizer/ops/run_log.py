from typing import Protocol
from uuid import UUID

import psycopg

# One row per (run, source) in ingestion_runs (db/schema/012). The helpers own
# their transactions so a failed source still commits its error row — the caller
# (load.py) rolls the connection back in that case, which would otherwise undo
# the very row recording the failure.

BEGIN_RUN_SQL = """
INSERT INTO ingestion_runs (run_id, source) VALUES (%s, %s)
"""

END_RUN_SQL = """
UPDATE ingestion_runs
   SET ended_at  = now(),
       fetched   = %s,
       inserted  = %s,
       updated   = %s,
       unchanged = %s,
       failed    = %s,
       error     = %s
 WHERE run_id = %s AND source = %s
"""


# The row-count shape load.LoadStats satisfies; declared here (not imported from
# load.py) so ingestion can import this module without a cycle. Read-only
# properties, not bare attributes: LoadStats is a frozen dataclass, whose members
# are read-only, and a Protocol with settable attributes would reject it.
class LoadCounts(Protocol):
    @property
    def inserted(self) -> int: ...
    @property
    def updated(self) -> int: ...
    @property
    def unchanged(self) -> int: ...
    @property
    def failed(self) -> int: ...


def begin_run(conn: psycopg.Connection, run_id: UUID, source: str) -> None:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(BEGIN_RUN_SQL, (run_id, source))


def end_run(
    conn: psycopg.Connection,
    run_id: UUID,
    source: str,
    fetched: int | None,
    stats: LoadCounts | None = None,
    error: str | None = None,
) -> None:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            END_RUN_SQL,
            (
                fetched,
                stats.inserted if stats is not None else None,
                stats.updated if stats is not None else None,
                stats.unchanged if stats is not None else None,
                stats.failed if stats is not None else None,
                error,
                run_id,
                source,
            ),
        )
