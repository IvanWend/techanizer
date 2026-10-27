import argparse
import asyncio
import glob
import json
import os
from collections import Counter
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.settings import ModelSettings

from techanizer.extraction.schema import DocType, PostingExtraction, RoleExtraction
from techanizer.extraction.source_adapters import (
    GroundTruth,
    apply_ground_truth,
    to_extraction_input,
)
from techanizer.extraction.transform import transform

load_dotenv()

CANDIDATES_PATH = "evals/gold_40_candidates.json"
OUTPUT_PATH = "evals/gold_labeled.json"

LABELER_MODEL = "deepseek:deepseek-v4-pro"
SAMPLES = 3
TEMPERATURE = 0.3
CONCURRENCY = 3

LABELER_PROMPT = """You are a ground-truth annotator for a job-posting extraction
eval. The user gives you a single job posting (plain text, HTML already stripped). Produce the
DEFINITIVE structured extraction — the answer a perfect model should give. This gold set is scored
against a weaker model's output, so a mistake silently poisons the eval. Be exhaustive and precise.

Rules:
- doc_type: "posting" for a job ad, "candidate" for someone advertising themselves, "other"
  otherwise. If not "posting", set company null and roles [].
- company: the hiring company's name as stated, or null. Never invent it.
- roles: ONE OBJECT PER DISTINCT ROLE, in the posting's own order. Do not merge two roles into
  one; do not split one role across locations.
- title: the role title as written, or null. Do not invent a title the text does not state.
- seniority: one bare token — intern | junior | mid | senior | staff+ | unknown.
  Staff/Principal/Lead/Head/Architect -> "staff+", Senior/Sr -> "senior", Mid/Middle -> "mid",
  Junior/Jr/Entry -> "junior", Intern/Trainee -> "intern", not stated -> "unknown".
  Russian: Ведущий/Руководитель -> staff+, Старший -> senior, Средний -> mid, Младший -> junior,
  Стажёр -> intern.
- stack: lowercase technologies/tools/languages/frameworks actually NAMED, deduplicated.
  A technology named in one role's own bullet belongs to THAT role only — never copy it onto
  other roles. A posting-wide stack stated once applies to every role (copy onto each).
- location: the work location as stated, or null.
- remote_policy: one bare token — remote | hybrid | onsite | unknown.
- employment_type: one bare token — full-time | part-time | contract | unknown.
- salary_min / salary_max: the stated bounds copied VERBATIM as written ("180k", "120000",
  "£60,000"), or null. Never convert or multiply. One bound only -> that field, the other null.
- salary_period: year | month | hour if stated, else null. State it even when a bound omits the
  word but the period is unambiguous.
- salary_currency: ISO code (USD/EUR/RUB/GBP/CAD/...) if stated, else null. A bare $ -> USD,
  £ -> GBP, € -> EUR, ₽/руб -> RUB.
- description: a verbatim ≤400-character span from the posting describing what THIS role does,
  or null. Copy character-for-character; prefer the role's own lines.
- Extract ONLY what the text states. If a field is not stated, leave it null (or "unknown" for
  seniority/remote_policy/employment_type). Never fabricate.
"""


class GoldRole(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    seniority: str | None = None
    stack: list[str] | None = None
    location: str | None = None
    remote_policy: str | None = None
    employment_type: str | None = None
    salary_min: str | float | None = None
    salary_max: str | float | None = None
    salary_period: str | None = None
    salary_currency: str | None = None
    description: str | None = None


class GoldPosting(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_type: DocType
    company: str | None = None
    roles: list[GoldRole] = []


# Held-out fields the board's own JSON owns; the labeler never decides them.
_HELD_OUT_ROLE_FIELDS = frozenset(
    {"title", "seniority", "stack", "location", "remote_policy", "employment_type",
     "salary_min", "salary_max", "salary_currency"}
)  # fmt: skip


def _mode(vals: list[Any]) -> Any:
    nonnull = [v for v in vals if v is not None]
    if not nonnull:
        return None
    return Counter(nonnull).most_common(1)[0][0]


def _stack_vote(stacks: list[list[str] | None]) -> list[str]:
    counts: Counter[str] = Counter()
    order: list[str] = []
    for st in stacks:
        for item in st or []:
            key = str(item).strip().casefold()
            if key not in counts:
                order.append(key)
            counts[key] += 1
    return [k for k in order if counts[k] >= 2]


def _vote_role(roles: list[GoldRole | None]) -> GoldRole:
    def field(name: str) -> Any:
        return _mode([getattr(r, name) if r is not None else None for r in roles])

    return GoldRole(
        title=field("title"),
        seniority=field("seniority") or "unknown",
        stack=_stack_vote([r.stack if r is not None else None for r in roles]),
        location=field("location"),
        remote_policy=field("remote_policy") or "unknown",
        employment_type=field("employment_type") or "unknown",
        salary_min=field("salary_min"),
        salary_max=field("salary_max"),
        salary_period=field("salary_period"),
        salary_currency=field("salary_currency"),
        description=field("description"),
    )


def reconcile(passings: list[GoldPosting]) -> GoldPosting:
    doc_type = _mode([p.doc_type for p in passings])
    company = _mode([p.company for p in passings])
    role_count = Counter(len(p.roles) for p in passings).most_common(1)[0][0]
    roles = []
    for i in range(role_count):
        roles_i = [p.roles[i] if i < len(p.roles) else None for p in passings]
        roles.append(_vote_role(roles_i))
    return GoldPosting(doc_type=doc_type, company=company, roles=roles)


def finalize(gold: GoldPosting, truth: GroundTruth) -> Any:
    # Blank held-out fields so the board's own JSON is the only source for them;
    # apply_ground_truth then fills them and names them in derived_fields.
    roles = []
    for r in gold.roles:
        d = r.model_dump()
        for name in truth.held_out & _HELD_OUT_ROLE_FIELDS:
            d[name] = None
        roles.append(RoleExtraction.model_construct(**d))
    company = None if "company" in truth.held_out else gold.company
    posting = PostingExtraction.model_construct(
        doc_type=gold.doc_type, company=company, roles=roles
    )
    return apply_ground_truth(transform(posting), truth)


async def _sample(agent: Agent[None, GoldPosting], text: str, attempts: int = 5) -> GoldPosting:
    # 429 / transient failures get a short backoff; the balance is low enough
    # that concurrency limits shrink, so a burst is expected to trip them.
    for i in range(attempts):
        try:
            result = await agent.run(
                text, model_settings=ModelSettings(temperature=TEMPERATURE, timeout=60)
            )
            return result.output
        except ModelHTTPError:
            if i == attempts - 1:
                raise
            await asyncio.sleep(2**i * 2)
    raise RuntimeError("unreachable: _sample loop must return or raise")


async def label_one(agent: Agent[None, GoldPosting], text: str, samples: int) -> GoldPosting:
    passings = await asyncio.gather(*(_sample(agent, text) for _ in range(samples)))
    return reconcile(list(passings))


async def run(
    rows: list[dict[str, Any]], model: str, samples: int, output_path: str
) -> dict[str, dict[str, Any]]:
    agent = Agent(
        model=model,
        system_prompt=LABELER_PROMPT,
        output_type=GoldPosting,
        retries=2,
    )
    sem = asyncio.Semaphore(CONCURRENCY)

    async def label_row(row: dict[str, Any]) -> tuple[dict[str, Any], Any]:
        payload = to_extraction_input(row["source"], row["external_id"], row["raw_text"])
        async with sem:
            gold = await label_one(agent, payload.text, samples)
        return row, finalize(gold, payload.ground_truth)

    out: dict[str, dict[str, Any]] = {}
    tasks = [asyncio.create_task(label_row(row)) for row in rows]
    done = 0
    for task in asyncio.as_completed(tasks):
        done += 1
        try:
            row, normalized = await task
            source, external_id = row["source"], row["external_id"]
            out.setdefault(source, {})[external_id] = normalized.model_dump()
            print(f"[{done}/{len(rows)}] {source} {external_id} -> {len(normalized.roles)} role(s)")
        except Exception as exc:  # noqa: BLE001 — a failed posting must not lose the rest
            print(f"[{done}/{len(rows)}] FAILED: {type(exc).__name__}: {exc}")
        # Incremental write: a mid-run crash keeps every posting already labelled.
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
    return out


def _to_gold(d: dict[str, Any]) -> GoldPosting:
    # Pass files from an external labeler carry source/external_id on the object;
    # strip them and keep only the fields GoldPosting/GoldRole declare.
    role_fields = set(GoldRole.model_fields)
    roles = [
        GoldRole(**{k: v for k, v in r.items() if k in role_fields}) for r in d.get("roles", [])
    ]
    return GoldPosting(doc_type=d["doc_type"], company=d.get("company"), roles=roles)


def run_from_passes(
    pass_paths: list[str], rows: list[dict[str, Any]], output_path: str
) -> dict[str, dict[str, Any]]:
    passes: list[dict[tuple[str, str], dict[str, Any]]] = []
    for path in pass_paths:
        arr = json.load(open(path, encoding="utf-8"))
        passes.append({(p["source"], p["external_id"]): p for p in arr})

    truth = {
        (r["source"], r["external_id"]): to_extraction_input(
            r["source"], r["external_id"], r["raw_text"]
        ).ground_truth
        for r in rows
    }

    out: dict[str, dict[str, Any]] = (
        json.load(open(output_path, encoding="utf-8")) if os.path.exists(output_path) else {}
    )
    for r in rows:
        key = (r["source"], r["external_id"])
        if not all(key in p for p in passes):
            print(f"{r['source']} {r['external_id']} -> MISSING in passes, skipped")
            continue
        passings = [_to_gold(p[key]) for p in passes]
        normalized = finalize(reconcile(passings), truth[key])
        out.setdefault(r["source"], {})[r["external_id"]] = normalized.model_dump()
        print(f"{r['source']} {r['external_id']} -> {len(normalized.roles)} role(s)")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", default=CANDIDATES_PATH)
    parser.add_argument("--output", default=OUTPUT_PATH)
    parser.add_argument("--samples", type=int, default=SAMPLES)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--model", default=LABELER_MODEL)
    parser.add_argument(
        "--passes",
        nargs="+",
        default=None,
        help="Reconcile pre-computed labeler pass files (globs ok) instead of calling the model.",
    )
    args = parser.parse_args()

    data = json.load(open(args.candidates, encoding="utf-8"))
    rows = data["rows"][: args.limit] if args.limit else data["rows"]

    if args.passes:
        paths = [p for pat in args.passes for p in sorted(glob.glob(pat))]
        gold = run_from_passes(paths, rows, args.output)
    else:
        gold = asyncio.run(run(rows, args.model, args.samples, args.output))

    print(f"Wrote {sum(len(v) for v in gold.values())} postings to {args.output}")


if __name__ == "__main__":
    main()
