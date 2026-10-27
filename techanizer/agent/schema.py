import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# A table cell is a number (counts, medians, salaries, similarities) or a label
# (skill, company, title, seniority, currency); null is the tool's own "no
# value", rendered as an explicit absence rather than a fabricated figure.
Cell = str | int | float | None


class Quote(BaseModel):
    id: int = Field(description="1-based citation number, referenced from key_finding as [n].")
    company: str | None = Field(
        default=None,
        description="Company, copied from the structured record.",
    )
    title: str | None = Field(
        default=None,
        description="Role title, copied from the structured record.",
    )
    source: str = Field(description="Board the posting came from (hn, remotive, web3, habr).")
    seniority: str | None = Field(
        default=None,
        description="Seniority level from the structured record.",
    )
    stack: list[str] = Field(default_factory=list, description="Skills from the structured record.")
    salary_min: int | None = Field(
        default=None,
        description="Monthly minimum salary, in its own currency.",
    )
    salary_max: int | None = Field(
        default=None,
        description="Monthly maximum salary, in its own currency.",
    )
    salary_currency: str | None = Field(
        default=None,
        description="ISO 4217 currency code, never converted.",
    )
    text: str = Field(
        description=(
            "A verbatim contiguous substring of the role's source_quotes (or its raw "
            "posting text), backing the claim it is attached to."
        ),
    )


# Which tool/query a table came from. Code owns the title and headers for each
# source (see agent/locale.py), so the stage-1 model emits only data rows and
# this enum — never labels. That makes the same table in two languages
# byte-identical, which is what the cross-language grader checks.
TableSource = Literal[
    "skill_frequency",
    "salary_by_currency",
    "salary_by_seniority",
    "top_salaries",
    "vector_search",
]


class TableSpec(BaseModel):
    kind: Literal["bars", "table"] = Field(
        description='"bars" for a skill-frequency ranking; "table" otherwise.'
    )
    source: TableSource = Field(
        description="The tool/query the rows came from; drives the localized title and headers."
    )
    rows: list[list[Cell]] = Field(
        default_factory=list,
        description=(
            "Data rows, copied exactly as the tools returned them — monthly, in their "
            "own currency, never converted or rounded."
        ),
    )


class Fact(BaseModel):
    key: str = Field(
        description="Placeholder name the prose references as {key}; short, ASCII, no digits."
    )
    value: int | float = Field(description="The number, copied from tool output.")
    kind: Literal["int", "percent"] = Field(
        default="int",
        description="How the value is locale-formatted when filled into prose.",
    )


class Payload(BaseModel):
    refused: bool = Field(
        default=False,
        description=(
            "True when the question is out of scope and no tool can answer it; "
            "tables, quotes and facts must then be empty."
        ),
    )
    tables: list[TableSpec] = Field(
        default_factory=list,
        description="At most 3 tables, each from tool output.",
    )
    quotes: list[Quote] = Field(
        default_factory=list,
        description="At most 5 quotes, each from tool output.",
    )
    facts: list[Fact] = Field(
        default_factory=list,
        description="Named numbers the prose will cite, copied from tool output.",
    )


# A digit is allowed only inside a [n] citation or a {key} placeholder; any
# other digit is a literal number the model must not write — code fills numbers
# from facts, locale-formatted. Rejecting here makes pydantic-ai retry rather
# than ship a literal figure.
_CITATION_OR_PLACEHOLDER = re.compile(r"\[[0-9]+\]|\{[^{}]*\}")


def _has_bare_digit(text: str) -> bool:
    return bool(re.search(r"\d", _CITATION_OR_PLACEHOLDER.sub("", text)))


class Prose(BaseModel):
    headline: str = Field(
        description=(
            "One sentence that directly answers the question, in the target locale; "
            "numbers only as {placeholders}."
        )
    )
    key_finding: str = Field(
        description=(
            "The strongest numeric claim, citing quotes as [n]; numbers only as {placeholders}."
        )
    )

    @field_validator("headline", "key_finding")
    @classmethod
    def _no_bare_digits(cls, v: str) -> str:
        if _has_bare_digit(v):
            raise ValueError(
                "prose must not contain literal digits; use {placeholders} for numbers"
            )
        return v


class Table(BaseModel):
    kind: Literal["bars", "table"]
    title: str
    headers: list[str] = Field(default_factory=list)
    rows: list[list[Cell]] = Field(default_factory=list)


class Answer(BaseModel):
    refused: bool = Field(
        default=False,
        description=(
            "True when the agent refused an out-of-scope question; headline is the "
            "localized refusal message and tables/quotes are empty."
        ),
    )
    headline: str
    key_finding: str
    tables: list[Table] = Field(default_factory=list)
    quotes: list[Quote] = Field(default_factory=list)
