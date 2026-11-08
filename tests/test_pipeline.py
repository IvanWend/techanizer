import os
from typing import cast

import psycopg
import pytest

from techanizer.extraction.pipeline import Outcome, _persist_guarded
from techanizer.extraction.schema import NormalizedRole


# Mirrors test_storage.writable_conn: writes are allowed only on the eval
# snapshot, never the live DB.
@pytest.fixture(scope="module")
def writable_conn():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        pytest.skip("DATABASE_URL unset")
    with psycopg.connect(db_url) as conn:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute("SELECT role FROM db_meta")
            row = cur.fetchone()
        if row is not None and row[0] == "live":
            pytest.skip("refusing to touch the live DB")
        yield conn


def test_persist_rejection_becomes_error_row(writable_conn) -> None:
    # The 2026-11-08 crash: an inverted band straight from the model killed the
    # whole run at the salary_band_ordered CHECK. transform now drops inverted
    # bands, so a hand-built one is the only road to the constraint left — and
    # it must end in an error row, not a raised exception.
    with writable_conn.transaction(), writable_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO raw_postings (source, external_id, raw_text, posted_at) "
            "VALUES ('habr', 'persist-guard-test', 'x', now()) RETURNING id"
        )
        inserted = cur.fetchone()
        assert inserted is not None
        raw_id = cast(int, inserted[0])
    try:
        outcome = Outcome(
            status="ok",
            model="test",
            doc_type="posting",
            roles=(
                NormalizedRole(
                    role_index=0, salary_min=1200, salary_max=300, salary_currency="USD"
                ),
            ),
        )

        returned = _persist_guarded(writable_conn, raw_id, outcome)

        assert returned.status == "error"
        assert returned.roles == ()
        assert "salary_band_ordered" in (returned.error or "")
        with writable_conn.cursor() as cur:
            cur.execute(
                "SELECT status, error FROM extraction_runs WHERE raw_posting_id = %s",
                (raw_id,),
            )
            stored = cur.fetchone()
        assert stored is not None
        assert stored[0] == "error"
        assert "salary_band_ordered" in stored[1]
    finally:
        with writable_conn.transaction(), writable_conn.cursor() as cur:
            cur.execute("DELETE FROM extraction_runs WHERE raw_posting_id = %s", (raw_id,))
            cur.execute("DELETE FROM raw_postings WHERE id = %s", (raw_id,))
