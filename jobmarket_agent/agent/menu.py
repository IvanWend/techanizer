"""Fixed-query menu: code builds the language-neutral Payload for each item.

The free-form /ask tool-calling stage answered out-of-scope questions with a
confident wrong answer, so it is retired (DECISIONS: Agent). Each menu item maps
1:1 to one analytics/vector_search call; the only model left is stage-2 prose,
which receives the payload plus the localized question and writes the headline
and key finding.
"""

from collections.abc import Sequence
from typing import Literal, assert_never, cast

from pydantic import BaseModel, ValidationError, model_validator

from jobmarket_agent.agent.locale import Lang
from jobmarket_agent.agent.schema import (
    Cell,
    Fact,
    Payload,
    Quote,
    Remote,
    Seniority,
    TableSource,
    TableSpec,
)
from jobmarket_agent.storage import analytics, role, vector_search
from jobmarket_agent.storage.analytics import TopSalary
from jobmarket_agent.storage.vector_search import SearchHit

QueryName = Literal[
    "skills",
    "salary_by_seniority",
    "salary_by_currency",
    "top_salaries",
    "similar_roles",
]


class SkillsParams(BaseModel):
    scope: Literal["overall", "seniority", "remote"] = "overall"
    seniority: Seniority | None = None
    remote: Remote | None = None
    limit: int = 10

    @model_validator(mode="after")
    def _scope_requires_filter(self) -> "SkillsParams":
        if self.scope == "seniority" and self.seniority is None:
            raise ValueError("scope 'seniority' requires a 'seniority' param")
        if self.scope == "remote" and self.remote is None:
            raise ValueError("scope 'remote' requires a 'remote' param")
        return self


class CurrencyParams(BaseModel):
    currency: str = "USD"
    limit: int = 5


class SimilarParams(BaseModel):
    phrase: str
    seniority: list[Seniority] | None = None
    remote: list[Remote] | None = None
    limit: int = 5


# salary_by_currency takes no params: it returns every currency, grouped.
class NoParams(BaseModel):
    pass


_PARAMS_MODEL: dict[QueryName, type[BaseModel]] = {
    "skills": SkillsParams,
    "salary_by_seniority": CurrencyParams,
    "salary_by_currency": NoParams,
    "top_salaries": CurrencyParams,
    "similar_roles": SimilarParams,
}


def validate_params(query: QueryName, params: dict) -> BaseModel:
    """Validate a params dict against the query's schema.

    Raises ValueError (not pydantic's ValidationError) so the serving request
    validator surfaces a clean 422, while a direct build_payload call fails with
    a plain, readable error.
    """
    try:
        return _PARAMS_MODEL[query].model_validate(params or {})
    except ValidationError as e:
        raise ValueError(f"invalid params for {query!r}: {e.errors()}") from e


def build_payload(query: QueryName, params: dict, conn) -> Payload:
    """Run the query's analytics/vector_search call and assemble its Payload."""
    typed = validate_params(query, params)
    if query == "skills":
        return build_skills(conn, cast(SkillsParams, typed))
    if query == "salary_by_seniority":
        return build_salary_by_seniority(conn, cast(CurrencyParams, typed))
    if query == "salary_by_currency":
        return build_salary_by_currency(conn)
    if query == "top_salaries":
        return build_top_salaries(conn, cast(CurrencyParams, typed))
    if query == "similar_roles":
        return build_similar_roles(conn, cast(SimilarParams, typed))
    assert_never(query)


def build_skills(conn, p: SkillsParams) -> Payload:
    source: TableSource
    if p.scope == "overall":
        rows = analytics.skill_frequency(conn, p.limit)
        source = "skill_frequency"
    elif p.scope == "seniority":
        assert p.seniority is not None  # SkillsParams validator guarantees it
        rows = analytics.skill_frequency_by_seniority(conn, p.seniority, p.limit)
        source = "skill_frequency_by_seniority"
    else:
        assert p.remote is not None  # SkillsParams validator guarantees it
        rows = analytics.skill_frequency_by_remote(conn, p.remote, p.limit)
        source = "skill_frequency_by_remote"
    cell_rows: list[list[Cell]] = [[s.skill, s.roles] for s in rows]
    table = TableSpec(kind="bars", source=source, rows=cell_rows)
    facts = [Fact(key="top_skill_roles", value=rows[0].roles)] if rows else []
    return Payload(tables=[table], facts=facts)


def build_salary_by_seniority(conn, p: CurrencyParams) -> Payload:
    rows = analytics.salary_by_seniority(conn, p.currency)
    cell_rows: list[list[Cell]] = [[r.seniority, r.roles, r.median_max_monthly] for r in rows]
    table = TableSpec(kind="table", source="salary_by_seniority", rows=cell_rows)
    medians = [r.median_max_monthly for r in rows if r.median_max_monthly is not None]
    facts = [Fact(key="top_median", value=max(medians))] if medians else []
    return Payload(tables=[table], facts=facts)


def build_salary_by_currency(conn) -> Payload:
    rows = analytics.salary_by_currency(conn)
    cell_rows: list[list[Cell]] = [[c.currency, c.n, c.median_max_monthly] for c in rows]
    table = TableSpec(kind="table", source="salary_by_currency", rows=cell_rows)
    named = [r for r in rows if r.currency]
    facts = [Fact(key="top_currency_roles", value=max(r.n for r in named))] if named else []
    return Payload(tables=[table], facts=facts)


def _salary_figure(r: TopSalary) -> int:
    # TOP_SALARIES_SQL filters rows where both salary_min and salary_max are NULL,
    # so one of these is non-None — same rule rank_top_salaries relies on.
    figure = r.salary_max if r.salary_max is not None else r.salary_min
    assert figure is not None
    return figure


def build_top_salaries(conn, p: CurrencyParams) -> Payload:
    rows = analytics.top_salaries(conn, p.currency, p.limit)
    cell_rows: list[list[Cell]] = [[r.company, r.title, _salary_figure(r)] for r in rows]
    table = TableSpec(kind="table", source="top_salaries", rows=cell_rows)
    quotes = _make_quotes(conn, rows, prefer="salary")
    facts = [Fact(key="top_salary", value=_salary_figure(rows[0]))] if rows else []
    return Payload(tables=[table], quotes=quotes, facts=facts)


def build_similar_roles(conn, p: SimilarParams) -> Payload:
    hits = vector_search.run(conn, p.phrase, limit=p.limit, seniority=p.seniority, remote=p.remote)
    cell_rows: list[list[Cell]] = [
        [h.company, h.title, h.seniority, round(h.similarity, 2)] for h in hits
    ]
    table = TableSpec(kind="table", source="vector_search", rows=cell_rows)
    quotes = _make_quotes(conn, hits, prefer="description")
    facts = [Fact(key="n_matches", value=len(hits))]
    return Payload(tables=[table], quotes=quotes, facts=facts)


def _make_quotes(
    conn, hits: Sequence[SearchHit | TopSalary], *, prefer: str, limit: int = 5
) -> list[Quote]:
    # Role-level items carry verbatim quotes; aggregates don't. A hit whose role
    # vanished between search and detail (or has no grounding text) is skipped so
    # the citation ids stay contiguous.
    quotes: list[Quote] = []
    for hit in hits[:limit]:
        detail = role.role_detail(conn, hit.raw_posting_id, hit.role_index)
        if detail is None:
            continue
        text = _quote_text(detail, prefer)
        if not text:
            continue
        quotes.append(
            Quote(
                id=len(quotes) + 1,
                company=detail.company,
                title=detail.title,
                source=detail.source,
                seniority=detail.seniority,
                stack=detail.stack,
                salary_min=detail.salary_min,
                salary_max=detail.salary_max,
                salary_currency=detail.salary_currency,
                text=text,
            )
        )
    return quotes


def _quote_text(detail: role.RoleDetail, prefer: str) -> str:
    # The claim picks the verbatim excerpt: top_salaries wants the salary line,
    # similar_roles wants the description. Fall back through other quote fields
    # so a role without the preferred key still grounds itself.
    quotes = detail.source_quotes
    for key in (prefer, "description", "salary", "title"):
        if quotes.get(key):
            return quotes[key]
    return next((v for v in quotes.values() if v), "")


_QUESTIONS: dict[QueryName, dict[Lang, str]] = {
    "skills": {
        "en": "Which skills appear in the most roles?",
        "ru": "Какие навыки упоминаются в наибольшем числе ролей?",
    },
    "salary_by_seniority": {
        "en": "What is the median {currency} salary by seniority?",
        "ru": "Какая медианная зарплата в {currency} по уровню?",
    },
    "salary_by_currency": {
        "en": "How do salaries compare across currencies?",
        "ru": "Как зарплаты различаются по валютам?",
    },
    "top_salaries": {
        "en": "Which are the highest-paid roles in {currency}?",
        "ru": "Какие роли самые высокооплачиваемые в {currency}?",
    },
    "similar_roles": {
        "en": 'Which roles are most similar to "{phrase}"?',
        "ru": "Какие роли больше всего похожи на «{phrase}»?",
    },
}


def question_for(query: QueryName, params: dict, lang: Lang) -> str:
    """Render the localized question the prose model answers.

    A scoped query (skills by seniority/remote) produces a table that doesn't
    name its own filter, so the question carries the filter value into the prose.
    """
    p = params or {}
    if query == "skills":
        scope = p.get("scope", "overall")
        if scope == "seniority":
            template = (
                "Which skills are most common among {value} roles?"
                if lang == "en"
                else "Какие навыки чаще всего встречаются у ролей уровня {value}?"
            )
            return template.format(value=p.get("seniority", ""))
        if scope == "remote":
            template = (
                "Which skills are most common among {value} roles?"
                if lang == "en"
                else "Какие навыки чаще всего встречаются у ролей формата {value}?"
            )
            return template.format(value=p.get("remote", ""))
        return _QUESTIONS["skills"][lang]
    return _QUESTIONS[query][lang].format(**p)
