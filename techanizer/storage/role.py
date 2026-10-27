from typing import NamedTuple


# Keyed on (raw_posting_id, role_index), not `id`: persist() is DELETE-then-INSERT
# so `id` is unstable across re-extraction (DECISIONS: storage). The agent gets
# this pair from a vector_search hit, which is all it ever sees.
class RoleDetail(NamedTuple):
    raw_posting_id: int
    role_index: int
    company: str | None
    title: str | None
    location: str | None
    seniority: str
    remote_policy: str
    employment_type: str
    stack: list[str]
    salary_min: int | None
    salary_max: int | None
    salary_currency: str | None
    description: str | None
    source_quotes: dict[str, str]
    source: str
    external_id: str


ROLE_DETAIL_SQL = """
SELECT s.raw_posting_id, s.role_index, s.company, s.title, s.location,
       s.seniority, s.remote_policy, s.employment_type, s.stack,
       s.salary_min, s.salary_max, s.salary_currency, s.description,
       s.source_quotes, r.source, r.external_id
FROM structured_postings s
JOIN raw_postings r ON r.id = s.raw_posting_id
WHERE s.raw_posting_id = %s AND s.role_index = %s
"""


def role_detail(conn, raw_posting_id: int, role_index: int) -> RoleDetail | None:
    with conn.cursor() as cur:
        cur.execute(ROLE_DETAIL_SQL, (raw_posting_id, role_index))
        row = cur.fetchone()
    return RoleDetail(*row) if row is not None else None
