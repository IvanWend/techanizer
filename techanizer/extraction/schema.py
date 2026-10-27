from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from techanizer.extraction.normalize import (
    Employment,
    RemotePolicy,
    Seniority,
    blank_to_none,
)

DocType = Literal["posting", "candidate", "other"]

# Fill-down targets: a role leaving one None inherits the posting's value.
INHERITABLE_FIELDS = (
    "stack",
    "description",
    "location",
    "remote_policy",
    "employment_type",
    "salary_min",
    "salary_max",
    "salary_period",
    "salary_currency",
)

# The four salary_* fields share one verbatim quote ("$150k/year"), so "salary"
# is a legal quote key even though it is not a field name.
SALARY_QUOTE_KEY = "salary"
QUOTE_KEY_ALIASES = frozenset({SALARY_QUOTE_KEY})

# Only where the spike actually fabricated: a title reconstructed from a
# truncated URL, and a salary on a posting stating none. Widening this sends the
# retry loop into a storm over header tokens nobody quotes cleanly.
QUOTE_REQUIRED = frozenset({"title", "company", "salary_min", "salary_max"})


class _Verbatim(BaseModel):
    """What the LLM emitted, before any conversion. Enum-shaped fields stay free
    strings here so a misread and an alias-map gap stay separately measurable."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_quotes: dict[str, str] = {}

    @field_validator("*", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        # blank_to_none ignores non-strings, which is what makes it safe to
        # wildcard over the list and dict fields too.
        return blank_to_none(value)

    @model_validator(mode="after")
    def _quote_keys_are_fields(self) -> Self:
        allowed = set(type(self).model_fields) | QUOTE_KEY_ALIASES
        invented = sorted(set(self.source_quotes) - allowed)
        if invented:
            raise ValueError(
                f"source_quotes keys must be field names of {type(self).__name__}; "
                f"invented: {invented}"
            )
        return self

    @model_validator(mode="after")
    def _required_quotes_present(self) -> Self:
        missing = set()
        for name in QUOTE_REQUIRED & set(type(self).model_fields):
            if getattr(self, name) is None:
                continue
            key = SALARY_QUOTE_KEY if name.startswith("salary_") else name
            if key not in self.source_quotes:
                missing.add(key)
        if missing:
            raise ValueError(f"non-null fields need a source quote: {sorted(missing)}")
        return self


class _Inheritable(_Verbatim):
    """The nine fields that fill down posting -> role. None at role level means
    inherit, not absent."""

    stack: list[str] | None = None
    # Verbatim span, so it grounds itself — deliberately not in QUOTE_REQUIRED,
    # which would store the same text twice.
    description: str | None = None
    location: str | None = None
    remote_policy: str | None = None
    employment_type: str | None = None
    salary_min: str | float | None = None
    salary_max: str | float | None = None
    salary_period: str | None = None
    salary_currency: str | None = None

    @model_validator(mode="after")
    def _salary_coherence(self) -> Self:
        if self.salary_min is None and self.salary_max is None:
            # Bookkeeping slip, not a misread — coerce rather than burn a retry.
            self.salary_period = None
            self.salary_currency = None
        return self


class RoleExtraction(_Inheritable):
    title: str | None = None
    seniority: str | None = None


class PostingExtraction(_Inheritable):
    doc_type: DocType
    company: str | None = None
    roles: list[RoleExtraction] = []

    @model_validator(mode="before")
    @classmethod
    def _clear_non_posting(cls, data: Any) -> Any:
        # Runs before field validation, so the quote checks never see a field
        # the doc_type rule is about to blank.
        if not isinstance(data, dict) or data.get("doc_type") in (None, "posting"):
            return data
        keep = {"doc_type", "source_quotes"}
        return {k: v if k in keep else ([] if k == "roles" else None) for k, v in data.items()}


class NormalizedRole(BaseModel):
    """One stored row of structured_postings, after fill-down and conversion."""

    model_config = ConfigDict(extra="forbid")

    role_index: int
    title: str | None = None
    seniority: Seniority = "unknown"
    stack: list[str] = []
    location: str | None = None
    remote_policy: RemotePolicy = "unknown"
    employment_type: Employment = "unknown"
    salary_min: int | None = None  # monthly
    salary_max: int | None = None  # monthly
    salary_currency: str | None = None  # ISO 4217, never converted
    description: str | None = None
    source_quotes: dict[str, str] = {}
    # Fields whose stored value did not come from the model's own field output —
    # recovered from its salary quote, or taken from the board's own JSON. The
    # eval scores the model, so it has to know which fields are not the model's.
    derived_fields: list[str] = []


class NormalizedPosting(BaseModel):
    # No inheritable fields: after fill-down they live on every role, and a
    # posting-level copy would be a second source of truth for Phase 3.
    model_config = ConfigDict(extra="forbid")

    doc_type: DocType
    company: str | None = None
    roles: list[NormalizedRole] = []
