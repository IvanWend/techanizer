import pytest

from techanizer.extraction.transform import resolve_salary

# Real bands and quotes from the corpus. The first element of each case is what
# the model put in the four salary_* fields, the second what it quoted.
PLAUSIBLE = (14583, 25000, "USD")
QUOTE = {"salary": "$175,000 - $300,000"}


@pytest.mark.parametrize(
    "band, quotes, expected",
    [
        # Fields the model got right are left alone, quote or no quote.
        (PLAUSIBLE, {}, PLAUSIBLE),
        (PLAUSIBLE, QUOTE, PLAUSIBLE),
        # salary_period omitted, so the fields converted to nothing — 429 rows of
        # the first corpus pass looked exactly like this.
        ((None, None, None), QUOTE, PLAUSIBLE),
        ((None, None, None), {"salary": "Comp competitive with top AI labs."}, (None, None, None)),
        ((None, None, None), {}, (None, None, None)),
        # "$300–450K": the low bound lost the multiplier its high bound carried.
        ((25, 37500, "USD"), {"salary": "$300–450K base, 0.35–0.70%"}, (25000, 37500, "USD")),
        # Same misread with no quote to recover from: NULL beats 25 a month.
        ((25, 37500, "USD"), {}, (None, None, None)),
    ],
)
def test_resolve_salary(
    band: tuple[int | None, int | None, str | None],
    quotes: dict[str, str],
    expected: tuple[int | None, int | None, str | None],
) -> None:
    assert resolve_salary(*band, quotes) == expected
