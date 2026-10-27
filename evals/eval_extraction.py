import argparse
import asyncio
import json
from collections import defaultdict

from dotenv import load_dotenv

from techanizer.extraction.pipeline import build_agent, extract
from techanizer.extraction.prompt import SYSTEM_PROMPT
from techanizer.extraction.source_adapters import to_extraction_input

load_dotenv()

CANDIDATES_PATH = "evals/gold_40_candidates.json"
GOLD_PATH = "evals/gold_labeled.json"

DEFAULT_MODEL = "deepseek:deepseek-v4-flash"
CONCURRENCY = 5

# Discrete fields scored exactly. `stack` is a set; salary is ints/strings.
SCALAR_FIELDS = ("title", "seniority", "location", "remote_policy", "employment_type")
SALARY_FIELDS = ("salary_min", "salary_max", "salary_currency")
# Web3 states amounts with no unit/period, Remotive's salary is free text mixing
# "$3k-$10k" with "$150k-$230k" — neither sits on a comparable monthly axis.
SKIP_SALARY_SOURCES = frozenset({"web3", "remotive"})


def _norm(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        return str(value)
    return value.strip().casefold()


def _eq_scalar(model_value, gold_value) -> bool:
    return _norm(model_value) == _norm(gold_value)


def _eq_stack(model_stack, gold_stack) -> bool:
    return set(model_stack or []) == set(gold_stack or [])


def score_role(model_role, gold_role, source) -> dict[str, bool]:
    # model_role is a NormalizedRole; gold_role is its JSON dict counterpart.
    derived = set(model_role.derived_fields)
    scores: dict[str, bool] = {}
    for f in SCALAR_FIELDS:
        if f not in derived:
            scores[f] = _eq_scalar(getattr(model_role, f), gold_role.get(f))
    if "stack" not in derived:
        scores["stack"] = _eq_stack(model_role.stack, gold_role.get("stack"))
    if source not in SKIP_SALARY_SOURCES:
        for f in SALARY_FIELDS:
            if f not in derived:
                scores[f] = _eq_scalar(getattr(model_role, f), gold_role.get(f))
    return scores


async def run(
    rows, gold, model
) -> tuple[
    dict[str, list[bool]],
    dict[str, list[tuple[str, str, str, str]]],
    dict[str, int],
    dict[str, int],
]:
    agent = build_agent(model, SYSTEM_PROMPT)
    sem = asyncio.Semaphore(CONCURRENCY)

    async def eval_row(row):
        payload = to_extraction_input(row["source"], row["external_id"], row["raw_text"])
        async with sem:
            outcome = await extract(agent, payload, model)
        return row, payload, outcome

    results: dict[str, list[bool]] = defaultdict(list)
    mismatches: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
    statuses: dict[str, int] = defaultdict(int)
    descriptions = {"gold": 0, "model": 0, "grounded": 0}

    for row, payload, outcome in await asyncio.gather(*(eval_row(r) for r in rows)):
        source, external_id = row["source"], row["external_id"]
        gold_posting = gold[source][external_id]
        if outcome.status != "ok":
            statuses[outcome.status] += 1
            print(f"{source} {external_id}: {outcome.status} — {outcome.error or ''}")
            continue

        statuses["ok"] += 1
        results["doc_type"].append(outcome.doc_type == gold_posting["doc_type"])

        roles = outcome.roles or ()
        if roles and "company" not in roles[0].derived_fields:
            ok = _eq_scalar(outcome.company, gold_posting["company"])
            results["company"].append(ok)
            if not ok:
                mismatches["company"].append(
                    (source, external_id, outcome.company, gold_posting["company"])
                )

        results["role_count"].append(len(roles) == len(gold_posting["roles"]))

        for i in range(min(len(roles), len(gold_posting["roles"]))):
            gold_role = gold_posting["roles"][i]
            for f, ok in score_role(roles[i], gold_role, source).items():
                results[f].append(ok)
                if not ok:
                    mismatches[f].append(
                        (source, external_id, str(getattr(roles[i], f)), str(gold_role.get(f)))
                    )
            if gold_role.get("description") is not None:
                descriptions["gold"] += 1
            if roles[i].description is not None:
                descriptions["model"] += 1
                if roles[i].description.strip() in payload.text:
                    descriptions["grounded"] += 1

    return results, mismatches, dict(statuses), descriptions


def report(results, mismatches, statuses, descriptions, total_postings, verbose) -> None:
    print(f"\npostings: {total_postings}  statuses: {statuses}")
    print(f"{'field':18} {'correct':>8} {'total':>6} {'rate':>8}")
    print("-" * 42)
    for field in ("doc_type", "role_count", "company", *SCALAR_FIELDS, "stack",
                  *SALARY_FIELDS):  # fmt: skip
        vals = results.get(field)
        if not vals:
            continue
        correct = sum(vals)
        print(f"{field:18} {correct:>8} {len(vals):>6} {correct / len(vals):>7.1%}")

    g, m, grounded = descriptions["gold"], descriptions["model"], descriptions["grounded"]
    if m:
        print(
            f"\ndescription: {m}/{g} gold-described roles produced a span; "
            f"{grounded}/{m} of those are verbatim substrings"
        )

    if verbose:
        print("\n=== mismatches (model -> gold) ===")
        for field, pairs in mismatches.items():
            seen: set[tuple[str, str]] = set()
            shown = 0
            for source, eid, mv, gv in pairs:
                if (mv, gv) in seen:
                    continue
                seen.add((mv, gv))
                print(f"{field:16} {source} {eid}: {mv!r} -> {gv!r}")
                shown += 1
                if shown >= 6:
                    break


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", default=CANDIDATES_PATH)
    parser.add_argument("--gold", default=GOLD_PATH)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    candidates = json.load(open(args.candidates, encoding="utf-8"))
    gold = json.load(open(args.gold, encoding="utf-8"))
    rows = candidates["rows"][: args.limit] if args.limit else candidates["rows"]

    results, mismatches, statuses, descriptions = asyncio.run(run(rows, gold, args.model))
    report(results, mismatches, statuses, descriptions, len(rows), args.verbose)


if __name__ == "__main__":
    main()
