import argparse
import logging
import os
from typing import NamedTuple

import psycopg
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

EMBED_MODEL = "bge-m3"
EMBED_DIM = 1024
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

# bge-m3 retrieval is asymmetric: the query carries this instruction, the stored
# role text (build_text) carries none. FlagEmbedding's shipped pattern for
# short-query -> long-passage search; documents stay un-prefixed.
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


class Role(NamedTuple):
    raw_posting_id: int
    role_index: int
    title: str | None
    seniority: str
    stack: list[str]
    description: str | None


class Stats(NamedTuple):
    embedded: int
    skipped: int


def _clean(s: str | None) -> str | None:
    if s is None:
        return None
    s = s.strip()
    return s or None


def build_text(role: Role) -> str | None:
    parts: list[str] = []

    title = _clean(role.title)
    if title is not None:
        parts.append(f"title: {title}")

    if role.seniority != "unknown":
        parts.append(f"seniority: {role.seniority}")

    stack = [t for t in (s.strip() for s in role.stack) if t]
    if stack:
        parts.append(f"stack: {', '.join(stack)}")

    description = _clean(role.description)
    if description is not None:
        parts.append(description)

    return "\n".join(parts) or None


def pending(conn, limit=None) -> list["Role"]:
    query = (
        "SELECT s.raw_posting_id, s.role_index, s.title, s.seniority, s.stack, s.description "
        "FROM structured_postings s "
        "WHERE NOT EXISTS ("
        "    SELECT 1 FROM posting_embeddings e "
        "    WHERE e.raw_posting_id = s.raw_posting_id "
        "      AND e.role_index     = s.role_index"
        ")"
    )
    query += " ORDER BY s.raw_posting_id, s.role_index"
    if limit:
        query += " LIMIT %s"
    with conn.cursor() as cur:
        cur.execute(query, (limit,) if limit else ())
        return [Role(*row) for row in cur.fetchall()]


def embed(texts: list[str]) -> list[list[float]]:
    resp = requests.post(
        f"{OLLAMA_BASE_URL}/api/embed",
        json={"model": EMBED_MODEL, "input": texts},
        timeout=120,
    )
    resp.raise_for_status()
    embeddings: list[list[float]] = resp.json()["embeddings"]
    assert len(embeddings) == len(texts)
    assert all(len(v) == EMBED_DIM for v in embeddings)
    return embeddings


def to_vector_text(vec: list[float]) -> str:
    return "[" + ",".join(map(str, vec)) + "]"


UPSERT_EMBEDDING_SQL = """
INSERT INTO posting_embeddings (raw_posting_id, role_index, embedding, embedded_text, embedded_at)
VALUES (%s, %s, %s::vector, %s, now())
ON CONFLICT (raw_posting_id, role_index) DO UPDATE
  SET embedding     = EXCLUDED.embedding,
      embedded_text = EXCLUDED.embedded_text,
      embedded_at   = now()
"""


def persist_embeddings(conn, rows: list[tuple[int, int, str, list[float]]]) -> None:
    if not rows:
        return
    # Tuple order matches the SQL placeholders: embedding (3rd) is the ::vector
    # text, embedded_text (4th) the string. Swap them and a title lands in vector().
    values = [(rid, ridx, to_vector_text(vec), text) for rid, ridx, text, vec in rows]
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(UPSERT_EMBEDDING_SQL, values)


def run(conn, roles: list[Role], chunk_size: int = 32) -> Stats:
    keep: list[tuple[Role, str]] = []
    skipped = 0
    for role in roles:
        text = build_text(role)
        if text is None:
            skipped += 1
        else:
            keep.append((role, text))

    # No per-role try/except: persist_embeddings commits per batch, so a crash
    # resumes from pending() on the next run — completed batches stay on disk.
    for i in range(0, len(keep), chunk_size):
        chunk = keep[i : i + chunk_size]
        vectors = embed([text for _, text in chunk])
        rows = [
            (role.raw_posting_id, role.role_index, text, vec)
            for (role, text), vec in zip(chunk, vectors, strict=True)
        ]
        persist_embeddings(conn, rows)
        logger.info("[%d/%d] embedded", i + len(chunk), len(keep))

    return Stats(embedded=len(keep), skipped=skipped)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Embed structured postings with bge-m3")
    parser.add_argument("--limit", type=int, default=None, help="Max roles to embed")
    parser.add_argument("--chunk-size", type=int, default=32, help="Roles per embed request")
    parser.add_argument("--dry-run", action="store_true", help="Preview without embedding")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = parse_args()

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        roles = pending(conn, limit=args.limit)
        logger.info("Found %d roles to embed.", len(roles))

        if args.dry_run:
            texts = [build_text(r) for r in roles]
            skipped = sum(1 for t in texts if t is None)
            logger.info("Dry run: %d roles, %d would be skipped (empty text).", len(roles), skipped)
            for r, t in zip(roles[:3], texts[:3], strict=True):
                logger.info("  [%s,%s] %s", r.raw_posting_id, r.role_index, (t or "<empty>")[:80])
            return

        stats = run(conn, roles, args.chunk_size)
        logger.info("Embedded %d roles, skipped %d (empty text).", stats.embedded, stats.skipped)


if __name__ == "__main__":
    main()
