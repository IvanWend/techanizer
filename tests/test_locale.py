import pytest
from pydantic import ValidationError

from techanizer.agent.locale import (
    TABLE_LABELS,
    fill_placeholders,
    format_int,
    format_percent,
)
from techanizer.agent.schema import Fact, Prose, TableSource

# Every TableSource must be localizable, or assemble() KeyErrors on a model that
# names a source the labels table forgot.
_ALL_SOURCES: set[TableSource] = {
    "skill_frequency",
    "salary_by_currency",
    "salary_by_seniority",
    "top_salaries",
    "vector_search",
}


def test_format_int_grouping() -> None:
    assert format_int(1604, "en") == "1,604"
    assert format_int(1604, "ru") == "1 604"


def test_format_percent_decimal() -> None:
    assert format_percent(24.5, "en") == "24.5%"
    assert format_percent(24.5, "ru") == "24,5%"


def test_fill_placeholders_resolves_facts() -> None:
    facts = [Fact(key="n_roles", value=1604), Fact(key="share", value=24.5, kind="percent")]
    assert fill_placeholders("{n_roles} roles", facts, "en") == "1,604 roles"
    assert fill_placeholders("{share} growth", facts, "ru") == "24,5% growth"


def test_fill_placeholders_rejects_unknown_key() -> None:
    with pytest.raises(ValueError):
        fill_placeholders("{missing} roles", [Fact(key="n_roles", value=1)], "en")


def test_prose_rejects_bare_digits() -> None:
    # A literal figure is a violation; pydantic-ai rejects (and retries) on it.
    with pytest.raises(ValidationError):
        Prose(headline="Python appears in 1604 roles", key_finding="Python leads [1]")
    # Citations [1] and {placeholders} are the only digits allowed.
    Prose(headline="Python is the top skill", key_finding="Python leads with {n} roles [1]")


def test_table_labels_cover_every_source() -> None:
    assert set(TABLE_LABELS) == _ALL_SOURCES
    for labels in TABLE_LABELS.values():
        assert set(labels) == {"en", "ru"}
        for title, headers in labels.values():
            assert isinstance(title, str) and title
            assert isinstance(headers, list)
