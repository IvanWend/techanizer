from collections.abc import Callable
from typing import Any, Literal, assert_never

from techanizer.storage import analytics, role
from techanizer.storage.vector_search import run

# The whitelist is the type: the model can only name one of these four, and
# pydantic-ai rejects anything else before it reaches this function. No SQL
# string ever crosses the model -> DB boundary.
QueryName = Literal["skill_frequency", "salary_by_currency", "salary_by_seniority", "top_salaries"]


def make_sql_query(conn) -> Callable[..., list[dict[str, Any]]]:
    # Closure, not a conn param: a conn in the signature would leak into the tool
    # schema the model sees. The build is pure (no conn use) — the contract test
    # passes a dummy object to prove it.
    def sql_query(
        query: QueryName,
        *,
        limit: int = 20,
        currency: str = "USD",
    ) -> list[dict[str, Any]]:
        """Answer a canned analytics question over the structured corpus.

        Median figures are monthly medians of salary_max, in the currency's own
        units (RUB rows read ~10^4, USD ~10^5 — never mix them). A null currency
        means the source never stated one: salary_by_currency withholds its median,
        and a salary_by_seniority split filters to the named currency and drops
        those roles entirely — never impute a currency to recover them.
        Report postings, not roles, when asked how many jobs want a skill.
        top_salaries ranks individual roles by salary_max, falling back to
        salary_min when max is null, ties broken by posting id — use it for
        "highest / best-paid" questions; vector_search ranks by similarity and
        cannot rank by salary. It filters to the named currency: pass the
        currency the question implies (e.g. RUB for Russia).

        Args:
            query: Which of the four hand-written analytics shapes to run.
            limit: Top-N rows returned, used by skill_frequency and top_salaries.
            currency: Currency for the salary splits and ranking, used by
                salary_by_seniority and top_salaries.
        """
        if query == "skill_frequency":
            return [r._asdict() for r in analytics.skill_frequency(conn, limit)]
        if query == "salary_by_currency":
            return [r._asdict() for r in analytics.salary_by_currency(conn)]
        if query == "salary_by_seniority":
            return [r._asdict() for r in analytics.salary_by_seniority(conn, currency)]
        if query == "top_salaries":
            return [r._asdict() for r in analytics.top_salaries(conn, currency, limit)]
        assert_never(query)

    return sql_query


def make_vector_search(conn) -> Callable[..., list[dict[str, Any]]]:
    # Closure, not a conn param: a conn in the signature would leak into the tool
    # schema the model sees. The build is pure (no conn use) — the contract test
    # passes a dummy object to prove it.
    def vector_search(
        query: str,
        *,
        limit: int = 10,
        seniority: list[Literal["intern", "junior", "mid", "senior", "staff+"]] | None = None,
        remote: list[Literal["remote", "hybrid", "onsite"]] | None = None,
    ) -> list[dict[str, Any]]:
        """Search roles by semantic similarity to a query.

        Hits are ranked by cosine similarity, higher = closer (max 1.0). Salary
        figures are monthly in their source currency, never converted. `stack`
        is a list of skills. Report similarity when asked which jobs are closest.

        Args:
            query: Free-text query, embedded and matched against role text.
            limit: Maximum number of hits to return.
            seniority: Restrict to these seniority levels; omit to search all.
            remote: Restrict to these remote policies; omit to search all.
        """
        hits = run(conn, query, limit=limit, seniority=seniority, remote=remote)
        return [r._asdict() for r in hits]

    return vector_search


def make_role_detail(conn) -> Callable[..., dict[str, Any] | None]:
    # Closure, not a conn param: a conn in the signature would leak into the tool
    # schema the model sees. The build is pure (no conn use) — the contract test
    # passes a dummy object to prove it.
    def role_detail(raw_posting_id: int, role_index: int) -> dict[str, Any] | None:
        """Fetch one role's full record by key.

        The key (raw_posting_id, role_index) comes from a vector_search hit, not
        the internal `id` column. `source_quotes` holds the verbatim text grounding
        each field. Salary figures are monthly in the role's own currency, never
        converted.

        Args:
            raw_posting_id: The posting id, as returned by a vector_search hit.
            role_index: Which role within that posting.
        """
        hit = role.role_detail(conn, raw_posting_id, role_index)
        return hit._asdict() if hit is not None else None

    return role_detail
