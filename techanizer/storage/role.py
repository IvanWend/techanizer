from typing import NamedTuple

from techanizer.extraction.source_adapters import posting_url


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
    url: str | None


ROLE_DETAIL_SQL = """
SELECT s.raw_posting_id, s.role_index, s.company, s.title, s.location,
       s.seniority, s.remote_policy, s.employment_type, s.stack,
       s.salary_min, s.salary_max, s.salary_currency, s.description,
       s.source_quotes, r.source, r.external_id, r.raw_text
FROM structured_postings s
JOIN raw_postings r ON r.id = s.raw_posting_id
WHERE s.raw_posting_id = %s AND s.role_index = %s
"""


def role_detail(conn, raw_posting_id: int, role_index: int) -> RoleDetail | None:
    with conn.cursor() as cur:
        cur.execute(ROLE_DETAIL_SQL, (raw_posting_id, role_index))
        row = cur.fetchone()
    if row is None:
        return None
    # raw_text rides the row only to derive the URL — the URL is reconstructed at
    # read time, never stored, so a re-snapshot needs no schema change.
    url = posting_url(row[-3], row[-2], row[-1])
    values = (*row[:-1], url)
    return RoleDetail(*values)


class QuoteLink(NamedTuple):
    url: str | None
    description: str | None


# A verbatim span shorter than this matches too many postings to identify one.
MIN_QUOTE_TEXT_MATCH = 20

# Leg 1 — the grader's triple: (source, company, title), text-ranked so a
# duplicate posting from the same company loses to the one the quote came from.
QUOTE_LINK_SQL = """
SELECT r.source, r.external_id, r.raw_text, s.description
FROM structured_postings s
JOIN raw_postings r ON r.id = s.raw_posting_id
WHERE r.source = %(source)s
  AND s.company = %(company)s
  AND s.title = %(title)s
ORDER BY (strpos(r.raw_text, %(text)s) > 0
          OR EXISTS (SELECT 1 FROM jsonb_each_text(s.source_quotes) AS q(k, v)
                     WHERE strpos(q.v, %(text)s) > 0)) DESC,
         s.raw_posting_id, s.role_index
LIMIT 1
"""

# Leg 2 — the verbatim span alone, for quotes whose company/title the model left
# empty: the text still identifies the posting (it is a substring of exactly the
# role's source_quotes).
QUOTE_LINK_BY_TEXT_SQL = """
SELECT r.source, r.external_id, r.raw_text, s.description
FROM structured_postings s
JOIN raw_postings r ON r.id = s.raw_posting_id
WHERE r.source = %(source)s
  AND (EXISTS (SELECT 1 FROM jsonb_each_text(s.source_quotes) AS q(k, v)
               WHERE strpos(q.v, %(text)s) > 0)
       OR strpos(r.raw_text, %(text)s) > 0)
ORDER BY s.raw_posting_id, s.role_index
LIMIT 1
"""


def _nul_free(value: str | None) -> str | None:
    # A model-emitted \u0000 survives JSON parsing but cannot be bound to a
    # Postgres text parameter; scrub it so a garbage quote can't fail the answer.
    return value.replace("\x00", "") if value is not None else None


def quote_link(
    conn, source: str, company: str | None, title: str | None, text: str = ""
) -> QuoteLink | None:
    """The posting URL and role description behind a quote. The model is an
    unreliable copier of optional url/description fields (Qwen drops them), so
    the link is resolved here: first by the (source, company, title) triple the
    verbatim grader uses, then — when the model left company/title empty — by
    the quote's own verbatim span. A quote that names no structured row gets
    None — a missing link, never a wrong or failed answer."""
    params = {
        "source": _nul_free(source) or "",
        "company": _nul_free(company),
        "title": _nul_free(title),
        "text": _nul_free(text) or "",
    }
    with conn.cursor() as cur:
        if company is not None and title is not None:
            cur.execute(QUOTE_LINK_SQL, params)
            row = cur.fetchone()
            if row is not None:
                return QuoteLink(posting_url(row[0], row[1], row[2]), row[3])
        if len(text.strip()) >= MIN_QUOTE_TEXT_MATCH:
            cur.execute(QUOTE_LINK_BY_TEXT_SQL, params)
            row = cur.fetchone()
            if row is not None:
                return QuoteLink(posting_url(row[0], row[1], row[2]), row[3])
    return None
