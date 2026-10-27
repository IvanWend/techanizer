import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from techanizer.extraction.schema import (
    INHERITABLE_FIELDS,
    QUOTE_REQUIRED,
    SALARY_QUOTE_KEY,
    DocType,
    NormalizedPosting,
    NormalizedRole,
    PostingExtraction,
    RoleExtraction,
)
from techanizer.extraction.source_adapters import to_extraction_input

# Values come from the corpus, not invention: gold_40_candidates.json rows for
# hn 48747990 ("We The Flywheel"), habr 1000167887 ("Top Selection", Разработчик
# Oracle) and the salary quote "$175,000 - $300,000" test_transform.py reuses.
_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "raw_responses.json"


def test_models_reject_unknown_fields() -> None:
    # extra="forbid" on every model: a typo'd field must not silently vanish.
    with pytest.raises(ValidationError):
        RoleExtraction(
            title="Agentic Engineer",
            source_quotes={"title": "Agentic Engineer"},
            invented="x",  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError):
        PostingExtraction(doc_type="posting", invented="x")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        NormalizedRole(role_index=0, invented="x")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        NormalizedPosting(doc_type="posting", invented="x")  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("  Berlin  ", "Berlin"),
        ("", None),
        ("  ", None),
        ("N/A", None),
        ("null", None),
        (None, None),
    ],
)
def test_verbatim_strips_and_blanks(raw: str | None, expected: str | None) -> None:
    # location is not in QUOTE_REQUIRED, so this tests blank/strip in isolation.
    assert RoleExtraction(location=raw).location == expected


def test_verbatim_does_not_normalize_stack() -> None:
    # str_strip_whitespace trims items, but aliasing ("Golang" -> "go") is
    # transform._stack's job — the verbatim layer keeps the model's raw tokens.
    assert RoleExtraction(stack=["Golang", "  React  "]).stack == ["Golang", "React"]


def test_quote_keys_must_be_field_names() -> None:
    with pytest.raises(ValidationError, match="invented"):
        RoleExtraction(title="Agentic Engineer", source_quotes={"tittle": "Agentic Engineer"})


def test_salary_is_a_legal_quote_key() -> None:
    # "salary" is not a field name but is the one allowed alias, so the four
    # salary_* fields can share a single verbatim quote.
    RoleExtraction(source_quotes={"salary": "$175,000 - $300,000"})


def test_company_requires_quote() -> None:
    with pytest.raises(ValidationError, match="source quote"):
        PostingExtraction(doc_type="posting", company="We The Flywheel")


def test_title_requires_quote() -> None:
    with pytest.raises(ValidationError, match="source quote"):
        RoleExtraction(title="Agentic Engineer")


def test_null_field_needs_no_quote() -> None:
    PostingExtraction(doc_type="posting")


def test_salary_requires_the_salary_key() -> None:
    # salary_min/salary_max ground themselves under the single "salary" key, not
    # their own field names.
    with pytest.raises(ValidationError, match="source quote"):
        RoleExtraction(salary_min="270000", salary_max="390000", source_quotes={"salary_min": "x"})
    RoleExtraction(
        salary_min="270000",
        salary_max="390000",
        source_quotes={"salary": "от 270 000 до 390 000 ₽"},
    )


def test_salary_coherence_clears_orphaned_period_and_currency() -> None:
    # A period/currency with no amount is a bookkeeping slip, not a misread —
    # coerce rather than burn a retry.
    role = RoleExtraction(salary_period="year", salary_currency="USD")
    assert (role.salary_period, role.salary_currency) == (None, None)


@pytest.mark.parametrize("doc_type", ["candidate", "other"])
def test_non_posting_blanks_fields_and_keeps_quotes(doc_type: DocType) -> None:
    posting = PostingExtraction(
        doc_type=doc_type,
        company="We The Flywheel",
        stack=["react"],
        salary_min="270000",
        roles=[
            RoleExtraction(title="Agentic Engineer", source_quotes={"title": "Agentic Engineer"})
        ],
        source_quotes={"company": "We The Flywheel", "salary": "от 270 000 до 390 000 ₽"},
    )
    assert posting.company is None
    assert posting.stack is None
    assert posting.salary_min is None
    assert posting.roles == []
    assert posting.source_quotes == {
        "company": "We The Flywheel",
        "salary": "от 270 000 до 390 000 ₽",
    }


def test_non_posting_skips_quote_checks() -> None:
    # _clear_non_posting runs before _required_quotes_present, so a candidate
    # that "set" company without a quote does not burn a retry.
    assert PostingExtraction(doc_type="candidate", company="We The Flywheel").company is None


def test_doc_type_required() -> None:
    with pytest.raises(ValidationError):
        PostingExtraction()  # type: ignore[call-arg]


def test_doc_type_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        PostingExtraction(doc_type="ad")  # type: ignore[arg-type]


def test_normalized_role_defaults() -> None:
    role = NormalizedRole(role_index=0)
    assert (role.seniority, role.remote_policy, role.employment_type) == (
        "unknown",
        "unknown",
        "unknown",
    )
    assert role.stack == []
    assert role.source_quotes == {}
    assert role.derived_fields == []
    assert (role.title, role.location, role.salary_min, role.salary_max) == (None, None, None, None)


def test_normalized_role_requires_role_index() -> None:
    with pytest.raises(ValidationError):
        NormalizedRole()  # type: ignore[call-arg]


def test_normalized_posting_requires_doc_type() -> None:
    with pytest.raises(ValidationError):
        NormalizedPosting()  # type: ignore[call-arg]


def test_contract_constants() -> None:
    # transform._inherit reads INHERITABLE_FIELDS by name and _required_quotes_present
    # maps salary_* onto SALARY_QUOTE_KEY; renaming any of these breaks fill-down
    # or grounding silently.
    assert INHERITABLE_FIELDS == (
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
    assert QUOTE_REQUIRED == frozenset({"title", "company", "salary_min", "salary_max"})
    assert SALARY_QUOTE_KEY == "salary"
    # description is a verbatim span, so it grounds itself — re-adding it would
    # store the same text twice (DECISIONS: schema).
    assert "description" not in QUOTE_REQUIRED


def test_fixtures_parse_to_extraction_inputs() -> None:
    # One real response per source, cached from the snapshot. Guards against a
    # fixture that drifted (truncated raw_text, dropped JSON key) between dumps.
    fixtures = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    assert set(fixtures) == {"hn", "habr", "web3", "remotive"}

    for row in fixtures.values():
        assert row["raw_text"]
        payload = to_extraction_input(row["source"], row["external_id"], row["raw_text"])
        assert payload.source == row["source"]
        assert payload.external_id == row["external_id"]
        assert payload.text.strip()
