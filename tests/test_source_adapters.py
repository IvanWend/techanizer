import pytest

from techanizer.extraction.schema import NormalizedPosting, NormalizedRole
from techanizer.extraction.source_adapters import GroundTruth, apply_ground_truth

# Habr external_id 1000167887 in evals/gold_40_candidates.json, run through
# to_extraction_input. None of it reaches the model: the prompt only ever sees
# description_html.
TOP_SELECTION = GroundTruth(
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
    company="Top Selection",
    title="Разработчик Oracle",
    stack=["oracle", "plsql", "git"],
    remote_policy="remote",
    employment_type="full-time",
    salary_min=270000,
    salary_max=390000,
    salary_currency="RUB",
    salary_period_known=True,
)


def _posting(role: NormalizedRole, company: str | None = None) -> NormalizedPosting:
    return NormalizedPosting(doc_type="posting", company=company, roles=[role])


def test_fills_every_gap_and_names_them() -> None:
    after = apply_ground_truth(_posting(NormalizedRole(role_index=0)), TOP_SELECTION)
    role = after.roles[0]

    assert after.company == "Top Selection"
    assert (role.title, role.remote_policy, role.employment_type) == (
        "Разработчик Oracle",
        "remote",
        "full-time",
    )
    assert (role.salary_min, role.salary_max, role.salary_currency) == (270000, 390000, "RUB")
    assert role.derived_fields == [
        "company",
        "employment_type",
        "remote_policy",
        "salary_currency",
        "salary_max",
        "salary_min",
        "stack",
        "title",
    ]
    # location is held out but this posting states none — a gap the board cannot
    # fill either stays a gap, and is not claimed as derived.
    assert role.location is None


def test_leaves_the_models_own_answers_alone() -> None:
    role = NormalizedRole(
        role_index=0,
        title="Oracle Developer",
        seniority="senior",
        stack=["oracle"],
    )
    after = apply_ground_truth(_posting(role, company="Top Selection"), TOP_SELECTION)

    assert after.company == "Top Selection"
    assert after.roles[0].title == "Oracle Developer"
    assert after.roles[0].stack == ["oracle"]
    assert "company" not in after.roles[0].derived_fields
    assert "title" not in after.roles[0].derived_fields


def test_board_json_outranks_a_quote_derived_band() -> None:
    # The salary quote said 100000-125000; the board's own JSON says otherwise,
    # and it is the more authoritative of the two.
    role = NormalizedRole(
        role_index=0,
        salary_min=100000,
        salary_max=125000,
        salary_currency="RUB",
        derived_fields=["salary_currency", "salary_max", "salary_min"],
    )
    after = apply_ground_truth(_posting(role), TOP_SELECTION)

    assert (after.roles[0].salary_min, after.roles[0].salary_max) == (270000, 390000)


@pytest.mark.parametrize(
    "posting, truth",
    [
        # HN holds nothing out — the whole comment is the input.
        (_posting(NormalizedRole(role_index=0)), GroundTruth()),
        (NormalizedPosting(doc_type="candidate"), TOP_SELECTION),
        (NormalizedPosting(doc_type="other"), TOP_SELECTION),
    ],
)
def test_no_op(posting: NormalizedPosting, truth: GroundTruth) -> None:
    assert apply_ground_truth(posting, truth) == posting
