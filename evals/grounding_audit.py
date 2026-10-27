import argparse
import json
import os
import re
import unicodedata
from collections import Counter
from typing import Any

import psycopg
from dotenv import load_dotenv

from techanizer.extraction.source_adapters import to_extraction_input

load_dotenv()


QUERY = """
SELECT s.raw_posting_id, s.role_index, r.source, r.external_id, r.raw_text,
       s.description, s.source_quotes
FROM structured_postings s
JOIN raw_postings r ON r.id = s.raw_posting_id
{where}
ORDER BY s.raw_posting_id, s.role_index
"""

# The model quotes from the cleaned text, but a curly apostrophe, an em dash or a
# run of newlines survives the round trip differently. Folding these is the
# difference between 1.7% misses and 30%.
FOLD = {
    "’": "'", "‘": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "−": "-", " ": " ",
}  # fmt: skip

# A bullet list rendered as one sentence-joined paragraph: every fragment
# verbatim, the whole not a span. Scored apart because it is a known mode, not a
# fabrication — the head landing means the model started from real text.
STITCH_HEAD = 40


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    for source, target in FOLD.items():
        text = text.replace(source, target)
    return re.sub(r"\s+", " ", text).strip().casefold()


def classify(value: str, haystack: str) -> str | None:
    needle = fold(value)
    if not needle or needle in haystack:
        return None
    return "stitched" if needle[:STITCH_HEAD] in haystack else "absent"


def audit(conn, sources: list[str] | None, limit: int | None) -> dict[str, Any]:
    where = "WHERE r.source = ANY(%s)" if sources else ""
    args: tuple[Any, ...] = (sources,) if sources else ()
    query = QUERY.format(where=where)
    if limit:
        query += " LIMIT %s"
        args += (limit,)

    checked: Counter[tuple[str, str]] = Counter()
    missed: Counter[tuple[str, str, str]] = Counter()
    misses: list[dict[str, Any]] = []
    texts: dict[int, str] = {}

    with conn.cursor() as cur:
        cur.execute(query, args)
        for raw_posting_id, role_index, source, external_id, raw_text, description, quotes in cur:
            if raw_posting_id not in texts:
                texts[raw_posting_id] = fold(
                    to_extraction_input(source, external_id, raw_text).text
                )
            haystack = texts[raw_posting_id]

            fields = {**(quotes or {})}
            if description:
                # Audited under its own key: it is a field the model fills with a
                # span, so it is grounded the same way a quote is.
                fields["description(field)"] = description

            for key, value in fields.items():
                if not isinstance(value, str) or not value.strip():
                    continue
                checked[(source, key)] += 1
                kind = classify(value, haystack)
                if kind is None:
                    continue
                missed[(source, key, kind)] += 1
                misses.append(
                    {
                        "raw_posting_id": raw_posting_id,
                        "role_index": role_index,
                        "source": source,
                        "field": key,
                        "kind": kind,
                        "value": value,
                    }
                )

    return {"checked": checked, "missed": missed, "misses": misses}


def _table(title: str, rows: list[tuple[str, int, int, int]]) -> str:
    lines = [f"\n{title}", f"{'':<20}{'checked':>9}{'stitched':>10}{'absent':>8}{'miss':>8}"]
    for name, checked, stitched, absent in rows:
        pct = 100 * (stitched + absent) / checked if checked else 0.0
        lines.append(f"{name:<20}{checked:>9}{stitched:>10}{absent:>8}{pct:>7.1f}%")
    return "\n".join(lines)


def report(result: dict[str, Any], examples: int) -> str:
    checked: Counter[tuple[str, str]] = result["checked"]
    missed: Counter[tuple[str, str, str]] = result["missed"]

    def rows(index: int) -> list[tuple[str, int, int, int]]:
        names = sorted({key[index] for key in checked}, key=lambda n: -_total(checked, index, n))
        return [
            (
                name,
                _total(checked, index, name),
                _kind(missed, index, name, "stitched"),
                _kind(missed, index, name, "absent"),
            )
            for name in names
        ]

    total = sum(checked.values())
    stitched = sum(c for (_, _, kind), c in missed.items() if kind == "stitched")
    absent = sum(c for (_, _, kind), c in missed.items() if kind == "absent")
    out = [
        (
            f"{total} values checked | {stitched + absent} not verbatim "
            f"({100 * (stitched + absent) / total:.1f}%) | stitched {stitched} | absent {absent}"
        ),
        _table("by field", rows(1)),
        _table("by source", rows(0)),
    ]

    if examples:
        out.append("\nabsent (not a stitched list — the model had no such text)")
        shown = [m for m in result["misses"] if m["kind"] == "absent"][:examples]
        for miss in shown:
            out.append(
                f"  {miss['raw_posting_id']}/{miss['role_index']} {miss['source']:<9}"
                f"{miss['field']:<20} {miss['value'][:80]!r}"
            )
    return "\n".join(out)


def _total(checked: Counter[tuple[str, str]], index: int, name: str) -> int:
    return sum(count for key, count in checked.items() if key[index] == name)


def _kind(missed: Counter[tuple[str, str, str]], index: int, name: str, kind: str) -> int:
    return sum(count for key, count in missed.items() if key[index] == name and key[2] == kind)


def parse_audit_args():
    parser = argparse.ArgumentParser(
        description="Substring-check every source_quote and description against the text the "
        "model actually saw."
    )
    parser.add_argument("--source", action="append", default=None, help="Repeatable source filter")
    parser.add_argument("--limit", type=int, default=None, help="Max role rows to check")
    parser.add_argument("--examples", type=int, default=15, help="Absent values to print")
    parser.add_argument("--json", default=None, help="Write every miss to this path")
    return parser.parse_args()


def main() -> None:
    args = parse_audit_args()
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        result = audit(conn, args.source, args.limit)

    print(report(result, args.examples))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(result["misses"], handle, ensure_ascii=False, indent=2)
        print(f"\n{len(result['misses'])} misses written to {args.json}")


if __name__ == "__main__":
    main()
