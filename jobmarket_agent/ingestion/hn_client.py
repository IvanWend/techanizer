from dataclasses import dataclass

import requests

ALGOLIA_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
ALGOLIA_ITEM_URL = "https://hn.algolia.com/api/v1/items"


@dataclass(frozen=True)
class HNComment:
    id: str
    text: str
    created_at: str


@dataclass(frozen=True)
class HNThread:
    story_id: str
    title: str
    created_at: str
    comments: list[HNComment]


def find_hiring_threads() -> list[tuple[str, str, str]]:
    """All 'Who is hiring' stories, newest-first, as (story_id, title, created_at)."""
    params: dict[str, str | int] = {
        "query": "who is hiring",
        "tags": "story,author_whoishiring",
        "hitsPerPage": 20,
    }
    resp = requests.get(ALGOLIA_SEARCH_URL, params=params, timeout=10)
    resp.raise_for_status()

    hits = resp.json()["hits"]
    threads = [
        (hit["objectID"], hit["title"], hit.get("created_at") or "")
        for hit in hits
        if "who is hiring" in hit["title"].lower()
    ]
    if not threads:
        raise RuntimeError(f"No 'Who is hiring' thread found in {len(hits)} newest posts")
    return threads


def fetch_thread(story_id: str) -> HNThread:
    resp = requests.get(f"{ALGOLIA_ITEM_URL}/{story_id}", timeout=30)
    resp.raise_for_status()
    payload = resp.json()

    comments: list[HNComment] = []
    for child in payload["children"]:
        # Deleted comments carry a null `text`; blank ones are not postings either.
        text = (child.get("text") or "").strip()
        created_at = child.get("created_at") or ""
        if not text:
            continue
        comments.append(HNComment(id=str(child["id"]), text=text, created_at=created_at))

    return HNThread(
        story_id=story_id,
        title=payload["title"],
        created_at=payload["created_at"],
        comments=comments,
    )


if __name__ == "__main__":
    threads = find_hiring_threads()
    print(f"threads: {len(threads)}")
    for story_id, title, created_at in threads:
        print(f"  {story_id}  {title}  ({created_at})")
