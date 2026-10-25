import re
from typing import Literal

from jobmarket_agent.agent.schema import Fact, TableSource

Lang = Literal["en", "ru"]

# Title + headers per table source, both locales. Stage 1 emits only a TableSpec
# (kind + source + rows); assemble() attaches these labels so the same payload
# renders localized without the model ever writing a label. Bars tables have no
# header row, so their headers list is empty.
TABLE_LABELS: dict[TableSource, dict[Lang, tuple[str, list[str]]]] = {
    "skill_frequency": {
        "en": ("Most-common skills", []),
        "ru": ("Самые частые навыки", []),
    },
    "skill_frequency_by_seniority": {
        "en": ("Most-common skills by seniority", []),
        "ru": ("Самые частые навыки по уровню", []),
    },
    "skill_frequency_by_remote": {
        "en": ("Most-common skills by remote", []),
        "ru": ("Самые частые навыки по формату", []),
    },
    "salary_by_currency": {
        "en": ("Salaries by currency", ["Currency", "Roles", "Median max/mo"]),
        "ru": ("Зарплаты по валютам", ["Валюта", "Ролей", "Медиана/мес"]),
    },
    "salary_by_seniority": {
        "en": ("Salary by seniority", ["Seniority", "Roles", "Median max/mo"]),
        "ru": ("Зарплаты по уровню", ["Уровень", "Ролей", "Медиана/мес"]),
    },
    "top_salaries": {
        "en": ("Highest-paid roles", ["Company", "Title", "Salary"]),
        "ru": ("Самые высокооплачиваемые роли", ["Компания", "Должность", "Зарплата"]),
    },
    "vector_search": {
        "en": ("Closest matches", ["Company", "Title", "Seniority", "Similarity"]),
        "ru": ("Ближайшие совпадения", ["Компания", "Должность", "Уровень", "Сходство"]),
    },
}


def format_int(value: int | float, lang: Lang) -> str:
    # EN groups thousands with commas (1,604); RU uses spaces (1 604).
    grouped = f"{int(value):,}"
    return grouped.replace(",", " ") if lang == "ru" else grouped


def format_percent(value: int | float, lang: Lang) -> str:
    # EN decimal point, RU decimal comma (24.5% vs 24,5%).
    text = f"{value}%"
    return text.replace(".", ",") if lang == "ru" else text


def format_value(value: int | float, kind: str, lang: Lang) -> str:
    return format_percent(value, lang) if kind == "percent" else format_int(value, lang)


_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")


def fill_placeholders(prose: str, facts: list[Fact], lang: Lang) -> str:
    # Replace every {key} with its locale-formatted value. An unknown key means
    # the stage-2 model invented a placeholder not in the payload — fail loud
    # rather than ship a dangling brace.
    by_key = {f.key: f for f in facts}

    def sub(match: re.Match[str]) -> str:
        key = match.group(1)
        fact = by_key.get(key)
        if fact is None:
            raise ValueError(f"unresolved placeholder {{{key}}}")
        return format_value(fact.value, fact.kind, lang)

    return _PLACEHOLDER.sub(sub, prose)
