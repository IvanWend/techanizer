import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict

from techanizer.extraction.normalize import (
    Employment,
    RemotePolicy,
    Seniority,
    currency_enum,
    employment_enum,
    html_to_text,
    normalize_stack,
    remote_policy_enum,
    seniority_enum,
    to_monthly,
)
from techanizer.extraction.schema import DocType, NormalizedPosting, NormalizedRole

# Unambiguous only. Anything needing judgment is doc_type's job, where the
# mistake is scored instead of invisible.
_HN_DEAD = re.compile(r"^\s*\[(flagged|dead)\]", re.IGNORECASE)
HN_MIN_CHARS = 120

_CURRENCY_SYMBOL = re.compile(r"[$€£₽₸₴]")


class GroundTruth(BaseModel):
    """Held-out structured fields, normalized into the same shape the extraction
    is scored in. `held_out` says which fields this source actually provides —
    without it, "source has no such field" is indistinguishable from "source
    says null", and the eval would punish correct nulls."""

    model_config = ConfigDict(extra="forbid")

    held_out: frozenset[str] = frozenset()
    company: str | None = None
    title: str | None = None
    seniority: Seniority | None = None
    stack: list[str] | None = None
    location: str | None = None
    remote_policy: RemotePolicy | None = None
    employment_type: Employment | None = None
    salary_min: int | None = None  # monthly, only when the period is known
    salary_max: int | None = None
    salary_currency: str | None = None
    # Web3 states amounts with no unit and no currency; Remotive's salary is
    # free text mixing "$3k - $10k" with "$150k - $230k". Neither can be put on
    # a monthly axis without a guess, so the amounts are carried unconverted and
    # the eval skips salary comparison when this is False.
    salary_period_known: bool = False
    salary_raw: str | None = None


class ExtractionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    external_id: str
    text: str
    ground_truth: GroundTruth = GroundTruth()
    # Set when the hard prefilter already decided; the LLM is skipped entirely.
    prefilter: DocType | None = None


def _titles(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    return [i["title"] for i in items if isinstance(i, dict) and i.get("title")]


def _bounds(low: Any, high: Any) -> str | None:
    # Which bound is which has to survive: a bare "12000.0" is unreadable as a
    # max, and this string is what a human labeller compares against.
    if low and high:
        return f"{low}-{high}"
    if high:
        return f"up to {high}"
    if low:
        return f"from {low}"
    return None


def _first_currency(text: str) -> str | None:
    m = _CURRENCY_SYMBOL.search(text or "")
    return currency_enum(m.group(0)) if m else None


def _hn(raw_text: str, external_id: str) -> ExtractionInput:
    text = html_to_text(raw_text)
    prefilter: DocType | None = None
    if _HN_DEAD.match(text) or len(text) < HN_MIN_CHARS:
        prefilter = "other"
    # HN is pure prose: the whole comment is the input and nothing is held out.
    return ExtractionInput(source="hn", external_id=external_id, text=text, prefilter=prefilter)


def _habr(raw: dict[str, Any], external_id: str) -> ExtractionInput:
    salary = raw.get("salary") or {}
    low, high = salary.get("from"), salary.get("to")
    company = raw.get("company") or {}
    gt = GroundTruth(
        held_out=frozenset(
            {
                "company",
                "title",
                "seniority",
                "stack",
                "location",
                "remote_policy",
                "employment_type",
                "salary_min",
                "salary_max",
                "salary_currency",
            }
        ),
        company=company.get("title") if isinstance(company, dict) else None,
        title=raw.get("title"),
        seniority=seniority_enum(raw.get("qualification")),
        stack=normalize_stack(_titles(raw.get("skills"))),
        location=", ".join(_titles(raw.get("locations"))) or None,
        remote_policy=remote_policy_enum(raw.get("remoteWork")),
        employment_type=employment_enum(raw.get("employment")),
        # Habr salaries are monthly by Russian convention — the one source that
        # pins a period, so the only one whose salary reaches the monthly axis.
        salary_min=to_monthly(low, "month") if low is not None else None,
        salary_max=to_monthly(high, "month") if high is not None else None,
        salary_currency=currency_enum(salary.get("currency")),
        salary_period_known=True,
        salary_raw=salary.get("formatted") or None,
    )
    return ExtractionInput(
        source="habr",
        external_id=external_id,
        text=html_to_text(raw.get("description_html")),
        ground_truth=gt,
    )


def _web3(raw: dict[str, Any], external_id: str) -> ExtractionInput:
    low, high = raw.get("salary_min_value"), raw.get("salary_max_value")
    gt = GroundTruth(
        held_out=frozenset({"company", "title", "stack", "location", "remote_policy"}),
        company=raw.get("company"),
        title=raw.get("title"),
        # estimated_* is the site's own guess, never ground truth.
        stack=normalize_stack(raw.get("tags") or []),
        location=raw.get("location"),
        remote_policy=remote_policy_enum(raw.get("is_remote")),
        salary_currency=currency_enum(raw.get("salary_currency")),
        salary_period_known=False,
        salary_raw=_bounds(low, high),
    )
    return ExtractionInput(
        source="web3",
        external_id=external_id,
        text=html_to_text(raw.get("description")),
        ground_truth=gt,
    )


def _remotive(raw: dict[str, Any], external_id: str) -> ExtractionInput:
    salary_raw = (raw.get("salary") or "").strip() or None
    gt = GroundTruth(
        held_out=frozenset({"company", "title", "stack", "location", "employment_type"}),
        company=raw.get("company_name"),
        title=raw.get("title"),
        stack=normalize_stack(raw.get("tags") or []),
        location=raw.get("candidate_required_location"),
        employment_type=employment_enum(raw.get("job_type")),
        salary_currency=_first_currency(salary_raw or ""),
        salary_period_known=False,
        salary_raw=salary_raw,
    )
    return ExtractionInput(
        source="remotive",
        external_id=external_id,
        text=html_to_text(raw.get("description")),
        ground_truth=gt,
    )


# Held out at role level. `company` is posting level and handled on its own.
_GROUND_TRUTH_ROLE_FIELDS = frozenset(
    {"title", "location", "seniority", "remote_policy", "employment_type", "stack"}
)
_SALARY_FIELDS = ("salary_min", "salary_max", "salary_currency")
# These three default to 'unknown' rather than None, so "empty" is not `is None`.
_ENUM_FIELDS = frozenset({"seniority", "remote_policy", "employment_type"})


def _is_empty(role: NormalizedRole, name: str) -> bool:
    # A field already derived from somewhere else is still open: the board's own
    # JSON outranks a figure re-read out of the model's salary quote, and this
    # keeps that true whichever fill runs first.
    if name in role.derived_fields:
        return True
    value = getattr(role, name)
    if name in _ENUM_FIELDS:
        return bool(value == "unknown")
    return value is None or value == []


def apply_ground_truth(posting: NormalizedPosting, truth: GroundTruth) -> NormalizedPosting:
    """Fill what the board states outright and the model could not have known.
    Only the posting body reaches the model on Habr, Web3 and Remotive, so a NULL
    company there means "not written in the body", not "not known" — every field
    in `held_out` is sitting in the raw JSON unused. Gaps only: a value the model
    did produce is left alone, and every field taken here is named in the role's
    `derived_fields` so the eval can still score the model on its own answers."""
    if posting.doc_type != "posting" or not truth.held_out:
        return posting

    company, company_filled = posting.company, False
    if "company" in truth.held_out and company is None and truth.company:
        company, company_filled = truth.company, True

    roles = []
    for role in posting.roles:
        update: dict[str, Any] = {}
        derived = ["company"] if company_filled else []

        for name in sorted(truth.held_out & _GROUND_TRUTH_ROLE_FIELDS):
            value = getattr(truth, name)
            if value in (None, [], "") or not _is_empty(role, name):
                continue
            update[name] = value
            derived.append(name)

        # All three or none: a currency without its band, or a band read off a
        # source that never stated the period, is worse than the NULL it replaces.
        if (
            truth.salary_period_known
            and set(_SALARY_FIELDS) & truth.held_out
            and all(_is_empty(role, name) for name in ("salary_min", "salary_max"))
            and (truth.salary_min is not None or truth.salary_max is not None)
        ):
            update.update({name: getattr(truth, name) for name in _SALARY_FIELDS})
            derived.extend(_SALARY_FIELDS)

        if derived:
            update["derived_fields"] = sorted({*role.derived_fields, *derived})
        roles.append(role.model_copy(update=update) if update else role)

    return posting.model_copy(update={"company": company, "roles": roles})


def to_extraction_input(source: str, external_id: str, raw_text: str) -> ExtractionInput:
    """The only place that knows source-specific JSON shape. If the eval script
    re-parses raw_text on its own the two drift and the scores stop meaning
    anything."""
    if source == "hn":
        return _hn(raw_text, external_id)

    raw = json.loads(raw_text)
    if source == "habr":
        return _habr(raw, external_id)
    if source == "web3":
        return _web3(raw, external_id)
    if source == "remotive":
        return _remotive(raw, external_id)
    raise ValueError(f"unknown source: {source!r}")
