from typing import Any

import pytest

from jobmarket_agent.extraction.normalize import (
    Employment,
    RemotePolicy,
    Seniority,
    alias,
    blank_to_none,
    currency_enum,
    employment_enum,
    fold_homoglyphs,
    html_to_text,
    normalize_stack,
    parse_salary_phrase,
    remote_policy_enum,
    salary_band_implausible,
    seniority_enum,
    to_monthly,
    to_number,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("180k", 180000.0),
        ("$180k", 180000.0),
        ("1.5M", 1500000.0),
        ("250 000", 250000.0),
        ("300к", 300000.0),
        (180000, 180000.0),
    ],
)
def test_to_number(raw: float | str, expected: float) -> None:
    assert to_number(raw) == expected


@pytest.mark.parametrize("raw", ["", "competitive", "от 75 000 ₽"])
def test_to_number_rejects(raw: str) -> None:
    with pytest.raises(ValueError):
        to_number(raw)


@pytest.mark.parametrize(
    "amount, period, expected",
    [
        (75000, "month", 75000),
        (270000, "month", 270000),
        ("$180k", "year", 15000),
        ("50", "hour", 8666),
    ],
)
def test_to_monthly(amount: float | str, period: str, expected: int) -> None:
    assert to_monthly(amount, period) == expected


def test_to_monthly_rejects_unknown_period() -> None:
    # Deliberate raise, not a monthly default: a default produces a
    # plausible-looking number nothing downstream can catch.
    with pytest.raises(ValueError, match="unknown salary period"):
        to_monthly(100, "week")


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Middle", "mid"),
        ("Senior", "senior"),
        ("Junior", "junior"),
        (None, "unknown"),
        ("Team Lead", "staff+"),
        ("стажёр", "intern"),
        # Provisional: compound seniority is still open in ROADMAP. Update when
        # the range-rounds-up rule lands.
        ("Mid-Senior/Senior", "unknown"),
    ],
)
def test_seniority_enum(raw: str | None, expected: Seniority) -> None:
    assert seniority_enum(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Habr and Remotive both send full_time; only the underscore fold in
        # employment_enum maps it, _EMPLOYMENT has no such key.
        ("full_time", "full-time"),
        ("contract", "contract"),
        (None, "unknown"),
        ("", "unknown"),
    ],
)
def test_employment_enum(raw: str | None, expected: Employment) -> None:
    assert employment_enum(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        (True, "remote"),
        # Not "onsite": a missing remote flag is not evidence of an office.
        (False, "unknown"),
        ("Worldwide", "remote"),
        ("USA, CST (UTC-6)", "unknown"),
        ("hybrid", "hybrid"),
    ],
)
def test_remote_policy_enum(raw: str | bool, expected: RemotePolicy) -> None:
    assert remote_policy_enum(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("rur", "RUB"),
        ("$", "USD"),
        (None, None),
        ("chf", "CHF"),
        ("xyz", None),
        ("Worldwide", None),
    ],
)
def test_currency_enum(raw: str | None, expected: str | None) -> None:
    assert currency_enum(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        # The containment guard: inline markup must not break a sentence, or a
        # quote spanning <strong> fails against the converted text.
        (
            "<p><strong>ИТ-Холдинг Т1 —\xa0</strong>один из лидеров</p>",
            "ИТ-Холдинг Т1 — один из лидеров",
        ),
        (
            '<div class="h3"><strong>Position:</strong> Account Executive</div>',
            "Position: Account Executive",
        ),
        ("line one<br/>line two", "line one\nline two"),
        ("<p>a</p><p>b</p><p>c</p>", "a\n\nb\n\nc"),
        ("<ul><li>one</li><li>two</li></ul>", "one\n\ntwo"),
        # Remotive pads with <p>&nbsp;</p>; the blank run must collapse, not
        # accumulate.
        ("<p>a</p>\n<p>\xa0</p>\n<p>b</p>", "a\n\nb"),
        ("   spaced   out   words   ", "spaced out words"),
        ("plain text no tags", "plain text no tags"),
        (None, ""),
        ("", ""),
    ],
)
def test_html_to_text(raw: str | None, expected: str) -> None:
    assert html_to_text(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("", None),
        ("  ", None),
        ("null", None),
        ("N/A", None),
        ("—", None),
        ("  Java  ", "Java"),
        (None, None),
        # Strings only: a numeric 0 is a real salary floor, and False has to
        # survive for remote_policy_enum's bool branch.
        (0, 0),
        (False, False),
        (75000, 75000),
    ],
)
def test_blank_to_none(raw: Any, expected: Any) -> None:
    assert blank_to_none(raw) == expected


def test_blank_to_none_extra() -> None:
    assert blank_to_none("Worldwide", extra=["worldwide"]) is None
    assert blank_to_none("Europe", extra=["worldwide"]) == "Europe"


@pytest.mark.parametrize(
    "raw, expected",
    [
        # 1С with a Cyrillic С folds to Latin; 33 corpus rows spell it this way.
        ("1С", "1C"),
        # Г is not a lookalike, so the fold leaves Cyrillic and is rejected whole.
        ("ГОСТ", "ГОСТ"),
        ("Управление рисками", "Управление рисками"),
        ("Docker", "Docker"),
        # Known false positive: every letter is a lookalike, so a real Russian
        # word Latinizes and the all-or-nothing guard cannot tell.
        ("РОСТ", "POCT"),
    ],
)
def test_fold_homoglyphs(raw: str, expected: str) -> None:
    assert fold_homoglyphs(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Golang", "go"),
        ("postgresql", "postgres"),
        ("Java Spring Framework", "spring"),
        ("Oracle PL/SQL", "plsql"),
        ("REST", "rest-api"),
        ("next.js", "nextjs"),
        ("Объектное хранилище S3", "s3"),
        ("ГОСТ", "gost"),
        ("1С", "1c"),
        ("Typescript ", "typescript"),
        # Cyrillic process/domain skills are dropped wholesale.
        ("Управление рисками", None),
        ("remote", None),
        ("non-tech", None),
        ("", None),
        (None, None),
        # Unmapped and un-stoplisted survives as-is, lowercased.
        ("Waterfall", "waterfall"),
        # Stoplisted as a phrase: "lead" alone does not match the web3 tag.
        ("team lead", None),
        # Skill alias v2 (PLAN step 3) — version tokens are stripped.
        ("Java 17+", "java"),
        ("python 3.10+", "python"),
        ("next.js 16", "nextjs"),
        ("OAuth 2.0", "oauth"),
        ("windows 10/11", "windows"),
        # Separator fold: whitespace/underscore/hyphen all collapse to "-".
        ("REST API", "rest-api"),
        ("rest_api", "rest-api"),
        ("react native", "react-native"),
        ("claude code", "claude-code"),
        ("GitHub Actions", "github-actions"),
        ("gitlab ci", "gitlab-ci"),
        # Typo tolerance — corpus-grounded misspellings and no-separator forms.
        ("postgesql", "postgres"),
        ("postgress", "postgres"),
        ("oauth2", "oauth"),
        ("restapi", "rest-api"),
        # Space-gate leaves no-space tokens alone; slash is left literal (a slash
        # is ambiguous — "ci/cd" is one skill, "docker/k8s" is two).
        ("c4", "c4"),
        ("ec2", "ec2"),
        ("html5", "html5"),
        ("docker/k8s", "docker/k8s"),
        ("tcp/ip", "tcp/ip"),
        ("c++23", "c++23"),
    ],
)
def test_alias(raw: str | None, expected: str | None) -> None:
    assert alias(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        (
            ["Управление рисками", "Waterfall", "Java", "Git", "Docker", "Java Spring Framework"],
            ["waterfall", "java", "git", "docker", "spring"],
        ),
        (
            ["remote", "defi", "erc-20", "smart-contract", "blockchain"],
            ["erc-20", "smart-contract"],
        ),
        # Aliasing collapses variants, and insertion order is preserved.
        (["Golang", "go", "golang", "postgresql", "postgres"], ["go", "postgres"]),
        # v2 collapses version + separator variants into one canonical too.
        (["Java 17+", "java", "REST api", "rest-api"], ["java", "rest-api"]),
        (["Java", None, "", "Java"], ["java"]),
        ([], []),
    ],
)
def test_normalize_stack(raw: list[str | None], expected: list[str]) -> None:
    assert normalize_stack(raw) == expected


@pytest.mark.parametrize(
    "quote, expected",
    [
        # Every phrase here is a source_quotes['salary'] value from the corpus.
        ("$175,000 - $300,000", (14583, 25000, "USD", "year")),
        # The multiplier is stated once, on the high bound.
        ("$300–450K base, 0.35–0.70%", (25000, 37500, "USD", "year")),
        ("55 - 85k EUR", (4583, 7083, "EUR", "year")),
        # A dot grouping thousands, a comma grouping thousands, in one phrase.
        ("$180.000-210,000/year + company equity", (15000, 17500, "USD", "year")),
        # The code wins over the symbol, or this reads as USD.
        ("$130,000 – $210,000 CAD", (10833, 17500, "CAD", "year")),
        ("AU$120–160k", (10000, 13333, "AUD", "year")),
        ("Зарплата от 220 000 ₽/мес.", (220000, None, "RUB", "month")),
        ("до 133 000 рублей на руки", (None, 133000, "RUB", "month")),
        ("💰Вилка: 270-400 т.р (гросс)", (270000, 400000, None, "month")),
        ("ЗП: 400–550 руб./час", (69332, 95332, "RUB", "hour")),
        ("Entry Level: $17.50/hr (with advancement opportunities)", (3033, 3033, "USD", "hour")),
        ("$178,500 USD + equity", (14875, 14875, "USD", "year")),
        ("$200K USD+", (16667, None, "USD", "year")),
        ("Compensation: $90k (junior) - $150k (senior)", (7500, 12500, "USD", "year")),
        ("1. Staff Software Engineer - $200K-275K + equity", (16667, 22917, "USD", "year")),
    ],
)
def test_parse_salary_phrase(quote: str, expected: tuple[Any, ...]) -> None:
    assert tuple(parse_salary_phrase(quote)) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "quote",
    [
        "$0 + equity",
        # No period stated and the magnitude fits either — guessing is the one
        # thing 005 forbids.
        "$12k–$15k",
        "Pay is competitive (9-12 LPA)",
        "Вознаграждение 10-20% с оборота проекта.",
        "$5 per referral who signs up",
        "Competitive salary + meaningful equity (2–5% range)",
        "Compensation: 100% Commission (Uncapped Earning Potential)",
        # A 28x band is a misread, not a band.
        "$10–$285K",
        "",
        None,
    ],
)
def test_parse_salary_phrase_rejects(quote: str | None) -> None:
    assert parse_salary_phrase(quote) is None


@pytest.mark.parametrize(
    "low, high, expected",
    [
        (14583, 25000, False),
        (None, 133000, False),
        (15000, None, False),
        (None, None, False),
        # "$300–450K" as the model first stored it: 300 and 450000, both legal.
        (25, 37500, True),
        # "$157-181K" with both bounds stripped of their multiplier.
        (13, 15, True),
    ],
)
def test_salary_band_implausible(low: int | None, high: int | None, expected: bool) -> None:
    assert salary_band_implausible(low, high) is expected
