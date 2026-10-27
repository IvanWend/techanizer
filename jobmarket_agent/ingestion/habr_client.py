import time

import requests
from bs4 import BeautifulSoup

HABR_LIST_URL = "https://career.habr.com/api/frontend/vacancies"
HABR_DETAIL_URL = "https://career.habr.com/vacancies/{vacancy_id}"

# The server caps a page at 50 rows. A larger per_page is echoed back verbatim in
# meta.perPage but still yields 50 — so page off meta.totalPages, never perPage.
HABR_PER_PAGE = 50

# Undocumented frontend API: identify the client and space the calls out. A full
# run is ~10 list calls plus one detail fetch per new posting.
HABR_USER_AGENT = "techanizer/0.1 (portfolio project)"
HABR_DELAY_S = 0.5

DESCRIPTION_SELECTOR = "div.vacancy-description__text"


def new_session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = HABR_USER_AGENT
    return session


def fetch_vacancy_cards(
    url: str,
    *,
    remote: bool = True,
    max_pages: int | None = None,
    session: requests.Session | None = None,
) -> list[dict]:
    session = session or new_session()

    params: dict[str, str | int] = {"type": "all", "per_page": HABR_PER_PAGE, "page": 1}
    if remote:
        params["remote"] = "true"

    cards: list[dict] = []
    page = 1
    total_pages = 1

    while page <= total_pages:
        params["page"] = page
        resp = session.get(url, params=params, timeout=10)
        resp.raise_for_status()
        payload = resp.json()

        if not (
            isinstance(payload, dict)
            and isinstance(payload.get("list"), list)
            and isinstance(payload.get("meta"), dict)
        ):
            raise RuntimeError(f"unexpected habr payload shape: {type(payload).__name__}")

        # Read the page count once, from the first response. Re-reading it every
        # iteration would let a posting published mid-run shift the total and
        # either drop a page or loop past the end.
        if page == 1:
            total_pages = int(payload["meta"]["totalPages"])
            if max_pages is not None:
                total_pages = min(total_pages, max_pages)

        cards.extend(payload["list"])
        page += 1
        if page <= total_pages:
            time.sleep(HABR_DELAY_S)

    return cards


def fetch_description_html(
    vacancy_id: str,
    *,
    session: requests.Session | None = None,
) -> str | None:
    session = session or new_session()

    resp = session.get(HABR_DETAIL_URL.format(vacancy_id=vacancy_id), timeout=10)
    # A vacancy archived between the list call and this one 404s; it is not an
    # error, there is just no prose to store.
    if resp.status_code == 404:
        return None
    resp.raise_for_status()

    block = BeautifulSoup(resp.text, "html.parser").select_one(DESCRIPTION_SELECTOR)
    if block is None:
        return None

    # Inner HTML verbatim, same replay-buffer rule as HN: stripping tags is a
    # lossy parse that Phase 2 should own.
    return block.decode_contents().strip()


if __name__ == "__main__":
    session = new_session()

    cards = fetch_vacancy_cards(HABR_LIST_URL, max_pages=1, session=session)
    print(f"Fetched {len(cards)} vacancy cards.")
    for card in cards[:5]:
        print(
            f"  {card['id']}  {card['publishedDate']['date']}  "
            f"{card['company']['title']} — {card['title'].strip()}"
        )

    if cards:
        vacancy_id = str(cards[0]["id"])
        html = fetch_description_html(vacancy_id, session=session)
        print(f"\ndescription for {vacancy_id}: {len(html) if html else 0} chars")
        print(f"verify: {HABR_DETAIL_URL.format(vacancy_id=vacancy_id)}")
