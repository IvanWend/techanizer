import re
from collections.abc import Iterable
from typing import Any, Literal, NamedTuple

from bs4 import BeautifulSoup

# Defined here, not in schema.py, so the import stays one-way: schema imports
# normalize, never the reverse.
Seniority = Literal["intern", "junior", "mid", "senior", "staff+", "unknown"]
Employment = Literal["full-time", "part-time", "contract", "unknown"]
RemotePolicy = Literal["remote", "hybrid", "onsite", "unknown"]

# [^\S\n] is whitespace except newline — \xa0 included, which Habr and Remotive
# prose is full of.
_HORIZONTAL_WS = re.compile(r"[^\S\n]+")
_LINE_EDGES = re.compile(r"[^\S\n]*\n[^\S\n]*")
_BLANK_RUN = re.compile(r"\n{3,}")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")

_BLOCK_TAGS = [
    "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "section", "article", "ul", "ol", "table",
]  # fmt: skip

HOURS_PER_MONTH = 173.33  # 2080/12
MONTHS_PER_YEAR = 12

_CURRENCY_CHARS = re.compile(r"[$€£₽₸₴¥₹]")
_MAGNITUDES = {"k": 1e3, "K": 1e3, "к": 1e3, "К": 1e3, "m": 1e6, "M": 1e6}

_PERIODS: dict[str, str] = {
    "year": "year", "yearly": "year", "yr": "year", "annual": "year",
    "annually": "year", "per year": "year", "/year": "year", "pa": "year",
    "month": "month", "monthly": "month", "mo": "month", "per month": "month",
    "/month": "month", "mth": "month",
    "hour": "hour", "hourly": "hour", "hr": "hour", "per hour": "hour", "/hour": "hour",
}  # fmt: skip

# --- free-text salary phrases -------------------------------------------------
# The model reports salary as four fields plus one verbatim quote; when it omits
# salary_period the fields are unusable and only the quote survives. These parse
# the quote back into a band.

# Where a comp phrase stops being salary: " + equity", ", plus bonus".
_SALARY_CUT = re.compile(r"\s\+\s|,(?!\d)")
_SALARY_LIST_MARKER = re.compile(r"^\s*\d+[.)]\s+")

# Numbers that are not pay. Applied after the cut, so "$300-450K base, 0.35-0.70%"
# keeps its band and loses only the equity percentage.
_SALARY_NOISE = re.compile(
    r"per\s+word|per\s+\d+\s+words|per\s+referral|per\s+contract|per\s+article"
    r"|commission|percentile|appointment|\bLPA\b|signing\s+bonus|paid\s+out"
    r"|\d\s*%|комисси|с\s+оборота|за\s+каждую",
    re.IGNORECASE,
)

_SALARY_AMOUNT = re.compile(
    r"(?P<num>\d{1,3}(?:[  .,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d+)?)"
    r"\s*(?P<mag>[kKmMкК]\b|т\.?\s?р\.?|тыс\.?)?"
)
_SALARY_MAGNITUDES = {"k": 1e3, "к": 1e3, "m": 1e6, "т.р": 1e3, "тр": 1e3, "тыс": 1e3}

_SALARY_CODE = re.compile(
    r"\b(us\$|au\$|ca\$|c\$|usd|eur|gbp|rub|rur|kzt|uah|byn|cad|chf|aud|inr|jpy|cny"
    r"|рубл\w*|руб\.?)",
    re.IGNORECASE,
)
_SALARY_RANGE_GAP = re.compile(r"[-–—]|\bto\b|\bдо\b", re.IGNORECASE)
_SALARY_OPEN_ENDED = re.compile(r"\s*(?:usd|eur|gbp)?\s*\+", re.IGNORECASE)
_SALARY_UPPER_ONLY = re.compile(r"up\s+to|\bдо\b|максимум|не\s+более", re.IGNORECASE)
_SALARY_LOWER_ONLY = re.compile(r"\bfrom\b|\bот\b|starting\s+at|начиная", re.IGNORECASE)

_SALARY_PERIOD_WORDS = (
    ("hour", re.compile(r"/\s*(?:hr|hour|час)|per\s+hour|an\s+hour|hourly|в\s+час", re.I)),
    (
        "month",
        re.compile(
            r"/\s*(?:mo|month|мес)|per\s+month|a\s+month|monthly"
            r"|в\s+месяц|ежемесячн",
            re.I,
        ),
    ),
    ("year", re.compile(r"/\s*(?:yr|year|год)|per\s+year|a\s+year|annual|в\s+год", re.I)),
)

# Habr states salary monthly by Russian convention, and a Cyrillic phrase is a
# Russian-market posting whatever currency it names — the one place a period can
# be inferred from language rather than magnitude.
_MONTHLY_CURRENCIES = frozenset({"RUB", "KZT", "UAH", "BYN"})
# Below this no monthly figure is pay — it is a band that lost its multiplier.
SALARY_FLOOR_MONTHLY = 300
_MONTH_BAND = (500, 3_000_000)
_YEAR_BAND = (20_000, 2_000_000)
# No real band spans 25x. Past that the phrase was misread, most often a low
# bound that lost the multiplier its high bound carried.
_SALARY_MAX_SPREAD = 25


class ParsedSalary(NamedTuple):
    salary_min: int | None  # monthly
    salary_max: int | None  # monthly
    currency: str | None
    period: str


BLANK_STRINGS = frozenset({"", "null", "none", "n/a", "na", "nil", "-", "—", "–"})

_SENIORITY: dict[str, Seniority] = {
    "intern": "intern", "internship": "intern", "trainee": "intern",
    "стажер": "intern", "стажёр": "intern", "стажировка": "intern",
    "junior": "junior", "jr": "junior", "entry": "junior", "entry-level": "junior",
    "джуниор": "junior", "джун": "junior", "младший": "junior",
    "mid": "mid", "middle": "mid", "mid-level": "mid", "intermediate": "mid",
    "миддл": "mid", "мидл": "mid", "средний": "mid",
    "senior": "senior", "sr": "senior", "senior-level": "senior",
    "синьор": "senior", "сеньор": "senior", "старший": "senior",
    "staff": "staff+", "staff+": "staff+", "principal": "staff+", "architect": "staff+",
    "lead": "staff+", "tech lead": "staff+", "team lead": "staff+", "teamlead": "staff+",
    "head": "staff+", "тимлид": "staff+", "ведущий": "staff+", "руководитель": "staff+",
}  # fmt: skip

_EMPLOYMENT: dict[str, Employment] = {
    "full-time": "full-time", "fulltime": "full-time", "full": "full-time",
    "permanent": "full-time", "полная занятость": "full-time", "полный день": "full-time",
    "part-time": "part-time", "parttime": "part-time", "part": "part-time",
    "частичная занятость": "part-time", "неполный день": "part-time",
    "contract": "contract", "contractor": "contract", "freelance": "contract",
    "temporary": "contract", "temp": "contract", "b2b": "contract",
    "фриланс": "contract", "подряд": "contract", "проектная работа": "contract",
}  # fmt: skip

# Habr states 'rur', a legacy code that is not ISO 4217. Symbols arrive from
# prose, three-letter codes from the boards.
_CURRENCIES: dict[str, str] = {
    "rur": "RUB", "rub": "RUB", "руб": "RUB", "руб.": "RUB", "р.": "RUB", "₽": "RUB",
    "usd": "USD", "$": "USD", "us$": "USD", "$usd": "USD", "долл": "USD",
    "cad": "CAD", "ca$": "CAD", "c$": "CAD", "aud": "AUD", "au$": "AUD",
    "eur": "EUR", "€": "EUR", "gbp": "GBP", "£": "GBP",
    "kzt": "KZT", "₸": "KZT", "uah": "UAH", "₴": "UAH", "byn": "BYN",
    "inr": "INR", "₹": "INR", "jpy": "JPY", "cny": "CNY", "¥": "CNY",
    "chf": "CHF",
}  # fmt: skip

_REMOTE: dict[str, RemotePolicy] = {
    "remote": "remote", "fully remote": "remote", "fully-remote": "remote",
    "100% remote": "remote", "100%-remote": "remote",
    "remote first": "remote", "remote-first": "remote",
    "wfh": "remote", "work from home": "remote", "work-from-home": "remote",
    "distributed": "remote", "anywhere": "remote", "worldwide": "remote",
    "удаленно": "remote", "удалённо": "remote",
    "удаленная работа": "remote", "удалённая работа": "remote",
    "hybrid": "hybrid", "partially remote": "hybrid", "partially-remote": "hybrid",
    "flexible": "hybrid", "гибрид": "hybrid", "гибридный": "hybrid",
    "onsite": "onsite", "on-site": "onsite", "on site": "onsite",
    "on-premise": "onsite", "on premise": "onsite",
    "in-office": "onsite", "in office": "onsite", "office": "onsite",
    "in-person": "onsite", "in person": "onsite", "local": "onsite",
    "офис": "onsite", "в офисе": "onsite",
}  # fmt: skip

# Seed, not a finished list. Grows from eval failures.
STACK_STOPLIST = frozenset({
    "open source", "opensource", "remote", "hybrid", "onsite", "full-time", "part-time",
    "contract", "internship", "entry-level", "junior", "senior", "lead", "non-tech",
    "engineer", "developer", "dev", "executive", "operations", "sales", "marketing",
    "crypto", "blockchain", "web3", "defi", "bitcoin", "ethereum",
    "united-states", "china", "europe", "worldwide", "usa", "uk", "team lead",
    "управление проектами", "управление рисками", "управление людьми",
    "управление разработкой", "планирование", "построение команды",
    "оптимизация бизнес-процессов", "информационная безопасность",
    "разработка программного обеспечения",
})  # fmt: skip

# Keys are post-casefold, post-homoglyph-fold; separator choice is free (the
# lookup folds them). Values are the canonical ids.
STACK_ALIASES: dict[str, str] = {
    "postgresql": "postgres", "psql": "postgres", "postgre": "postgres",
    "postgesql": "postgres", "postgress": "postgres",  # corpus typos
    "k8s": "kubernetes", "golang": "go", "node": "nodejs", "node.js": "nodejs",
    "js": "javascript", "ts": "typescript", "py": "python",
    "c#": "csharp", "c++": "cpp", "objective-c": "objc",
    "amazon web services": "aws", "google cloud": "gcp",
    "google cloud platform": "gcp", "microsoft azure": "azure",
    "react.js": "react", "reactjs": "react", "vue.js": "vue", "vuejs": "vue",
    "next.js": "nextjs", "rest": "rest-api", "restful": "rest-api", "restapi": "rest-api",
    "oauth2": "oauth",
    "ci/cd": "ci-cd", "cicd": "ci-cd",
    "java spring framework": "spring", "spring boot": "spring",
    "oracle pl/sql": "plsql", "pl/sql": "plsql",
    "гост": "gost", "объектное хранилище s3": "s3", "1с": "1c",
}  # fmt: skip

# `1С` appears with a Cyrillic С in 33 corpus rows and a Latin C in 48.
_LOOKALIKES = str.maketrans("АВЕКМНОРСТУХаеорсух", "ABEKMHOPCTYXaeopcyx")

# --- skill alias v2 (PLAN step 3) -------------------------------------------
# Version tokens and separator choice each fragment one skill into many: "java
# 17+", "java 11" and "java" are the same skill, and so are "rest api",
# "rest_api" and "rest-api". v2 strips a trailing version token and folds
# whitespace/underscore/hyphen runs to one hyphen. Slash and dot stay literal: a
# slash is ambiguous ("ci/cd" is one skill, "docker/k8s" is two) and a dot
# anchors node.js/react.js.

# Trailing version token: "17", "17+", "3.10+", "10/11", "8.x". Anchored to the
# end and gated on the leading space, so "ec2", "html5", "c++23", "g.729" are
# never stripped.
_VERSION_SUFFIX = re.compile(r"\s+\d+(?:[.+/]\d+)*(?:\.x)?\+?$")

# Runs of whitespace, underscore or hyphen collapse to a single hyphen.
_SEPARATOR_RUN = re.compile(r"[\s_-]+")


def _fold(key: str) -> str:
    return _SEPARATOR_RUN.sub("-", key).strip("-")


# Stoplist and alias keys are folded once so the lookup sees the same canonical
# form as the input — whoever adds a key can write it with any separator.
_STOPLIST = frozenset(_fold(k) for k in STACK_STOPLIST)
_ALIASES = {_fold(k): v for k, v in STACK_ALIASES.items()}


def html_to_text(html: str | None) -> str:
    if not html:
        return ""

    soup = BeautifulSoup(html, "html.parser")
    # Newlines per block tag, not get_text(separator=): a separator splits at
    # every string boundary, so inline markup breaks a sentence across lines and
    # any quote spanning it fails containment.
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.insert_before("\n\n")
    for tag in soup.find_all("br"):
        tag.replace_with("\n")

    text = soup.get_text()
    text = _HORIZONTAL_WS.sub(" ", text)
    text = _LINE_EDGES.sub("\n", text)
    text = _BLANK_RUN.sub("\n\n", text)
    return text.strip()


def blank_to_none(value: Any, extra: Iterable[str] = ()) -> Any:
    # Strings only — a numeric 0 is a real salary floor, not a blank.
    if not isinstance(value, str):
        return value

    stripped = value.strip()
    key = stripped.casefold()
    if key in BLANK_STRINGS:
        return None
    if any(key == item.casefold() for item in extra):
        return None
    return stripped


def to_number(value: float | str) -> float:
    if not isinstance(value, str):
        return float(value)

    # The LLM extracts verbatim, so "$180k" and "1.5M" arrive as written. Bare
    # float() rejects both, which would silently cost every HN salary.
    cleaned = _CURRENCY_CHARS.sub("", value).replace(",", "")
    cleaned = _HORIZONTAL_WS.sub("", cleaned).strip()
    multiplier = 1.0
    if cleaned and cleaned[-1] in _MAGNITUDES:
        multiplier = _MAGNITUDES[cleaned[-1]]
        cleaned = cleaned[:-1]
    return float(cleaned) * multiplier


def to_monthly(amount: float | str, period: str) -> int:
    unit = _PERIODS.get(str(period).strip().casefold())
    # Raise rather than default to monthly: a silent default produces a
    # plausible-looking number nothing downstream can catch.
    if unit is None:
        raise ValueError(f"unknown salary period: {period!r}")

    value = to_number(amount)
    if unit == "year":
        value /= MONTHS_PER_YEAR
    elif unit == "hour":
        value *= HOURS_PER_MONTH
    return round(value)


def salary_band_implausible(low: int | None, high: int | None) -> bool:
    stated = [v for v in (low, high) if v is not None]
    if not stated:
        return False
    if min(stated) < SALARY_FLOOR_MONTHLY:
        return True
    return bool(low and high and high / low >= _SALARY_MAX_SPREAD)


def _salary_currency(phrase: str) -> str | None:
    # A code beats a symbol wherever it appears: "$130,000 - $210,000 CAD" is
    # CAD, and reading the symbol first would silently call it USD.
    code = _SALARY_CODE.search(phrase)
    if code:
        token = code.group(1).casefold()
        return currency_enum("руб" if token.startswith("руб") else token)
    symbol = _CURRENCY_CHARS.search(phrase)
    return currency_enum(symbol.group(0)) if symbol else None


def _salary_amount(match: "re.Match[str]") -> tuple[float, float]:
    num = match.group("num")
    # A separator followed by exactly three digits groups thousands; anything
    # else is a decimal point. "200.000" is 200k, "17.50" is not.
    tail = re.search(r"[ \u00a0.,](\d+)$", num)
    if tail and len(tail.group(1)) != 3:
        head = re.sub(r"[ \u00a0.,]", "", num[: tail.start()])
        value = float(f"{head}.{tail.group(1)}")
    else:
        value = float(re.sub(r"[ \u00a0.,]", "", num))

    mag = match.group("mag")
    key = mag.casefold().replace(" ", "").rstrip(".") if mag else ""
    return value, _SALARY_MAGNITUDES.get(key, 1.0)


def parse_salary_phrase(text: str | None, currency: str | None = None) -> ParsedSalary | None:
    if not text:
        return None

    phrase = _HORIZONTAL_WS.sub(" ", text.replace("\n", " ")).strip()
    phrase = _SALARY_LIST_MARKER.sub("", phrase)

    first = _SALARY_AMOUNT.search(phrase)
    if first is None:
        return None
    # Cut only past the first amount: a comma inside Russian prose arrives long
    # before the figure does, and cutting there throws the figure away.
    cut = _SALARY_CUT.search(phrase, first.end())
    if cut:
        phrase = phrase[: cut.start()]
    if _SALARY_NOISE.search(phrase):
        return None

    matches = list(_SALARY_AMOUNT.finditer(phrase))

    low_value, low_mult = _salary_amount(matches[0])
    low: float | None = low_value * low_mult
    high: float | None = None

    if len(matches) > 1:
        gap = phrase[matches[0].end() : matches[1].start()]
        if len(gap) <= 24 and _SALARY_RANGE_GAP.search(gap):
            high_value, high_mult = _salary_amount(matches[1])
            # "$300–450K" states the multiplier once, on the high bound. Without
            # this the band lands as 300 to 450000 and passes every constraint.
            if low_mult == 1.0 and high_mult > 1.0 and low_value < high_value:
                low = low_value * high_mult
            high = high_value * high_mult

    if high is None:
        head = phrase[: matches[0].start()]
        if _SALARY_OPEN_ENDED.match(phrase[matches[0].end() :]):
            pass
        elif _SALARY_UPPER_ONLY.search(head):
            low, high = None, low
        elif not _SALARY_LOWER_ONLY.search(head):
            high = low

    stated = [v for v in (low, high) if v is not None]
    if not stated or min(stated) <= 0:
        return None

    period = next((name for name, pattern in _SALARY_PERIOD_WORDS if pattern.search(text)), None)
    currency = _salary_currency(phrase) or currency
    if period is None:
        monthly_market = currency in _MONTHLY_CURRENCIES or bool(_CYRILLIC.search(text))
        band, period = (_MONTH_BAND, "month") if monthly_market else (_YEAR_BAND, "year")
        if not band[0] <= max(stated) <= band[1]:
            return None

    monthly_low = to_monthly(low, period) if low is not None else None
    monthly_high = to_monthly(high, period) if high is not None else None
    converted = [v for v in (monthly_low, monthly_high) if v is not None]
    if min(converted) < SALARY_FLOOR_MONTHLY or max(converted) > 5_000_000:
        return None
    if monthly_low and monthly_high and monthly_high / monthly_low >= _SALARY_MAX_SPREAD:
        return None

    return ParsedSalary(monthly_low, monthly_high, currency, period)


def fold_homoglyphs(text: str) -> str:
    folded = text.translate(_LOOKALIKES)
    # Only accept a fold that removes Cyrillic entirely; a partial fold of a
    # Russian word leaves mixed-script garbage.
    return folded if not _CYRILLIC.search(folded) else text


def alias(item: str | None) -> str | None:
    cleaned = blank_to_none(item)
    if cleaned is None:
        return None

    key = _HORIZONTAL_WS.sub(" ", fold_homoglyphs(cleaned)).strip().casefold()
    key = _VERSION_SUFFIX.sub("", key)
    key = _fold(key)
    if key in _STOPLIST:
        return None
    if key in _ALIASES:
        return _ALIASES[key]
    # Habr's Cyrillic skills are process/domain, not stack. Anything Cyrillic
    # worth keeping is mapped above.
    if _CYRILLIC.search(key):
        return None
    return key


def normalize_stack(items: Iterable[str | None]) -> list[str]:
    seen: dict[str, None] = {}
    for item in items:
        canonical = alias(item)
        if canonical is not None:
            seen.setdefault(canonical, None)
    return list(seen)


def seniority_enum(value: str | None) -> Seniority:
    cleaned = blank_to_none(value)
    if cleaned is None:
        return "unknown"

    key = _HORIZONTAL_WS.sub(" ", cleaned).strip().casefold().replace("_", "-")
    return _SENIORITY.get(key, "unknown")


def employment_enum(value: str | None) -> Employment:
    cleaned = blank_to_none(value)
    if cleaned is None:
        return "unknown"

    key = _HORIZONTAL_WS.sub(" ", cleaned).strip().casefold().replace("_", "-")
    return _EMPLOYMENT.get(key, "unknown")


def remote_policy_enum(value: str | bool | None) -> RemotePolicy:
    if isinstance(value, bool):
        return "remote" if value else "unknown"

    cleaned = blank_to_none(value)
    if cleaned is None:
        return "unknown"

    key = _HORIZONTAL_WS.sub(" ", cleaned).strip().casefold().replace("_", "-")
    return _REMOTE.get(key, "unknown")


def currency_enum(value: str | None) -> str | None:
    # Annotated because this is the one enum helper that returns a derived
    # string rather than a Literal, so blank_to_none's Any would leak out.
    cleaned: str | None = blank_to_none(value)
    if cleaned is None:
        return None

    key = cleaned.strip().casefold()
    return _CURRENCIES.get(key)
