import os

import requests
from dotenv import load_dotenv

load_dotenv()

WEB3_ENDPOINT_URL = "https://web3.career/api/v1"


WEB3_MAX_LIMIT = 100


def fetch_latest_jobs(
    url: str,
    api_token: str | None,
    *,
    remote: bool = True,
    limit: int = WEB3_MAX_LIMIT,
    tag: str | None = None,
) -> list[dict]:
    if not api_token:
        raise RuntimeError("WEB3_API_KEY is not set")

    params: dict[str, str | int] = {"token": api_token, "limit": min(limit, WEB3_MAX_LIMIT)}
    if remote:
        params["remote"] = "true"
    if tag:
        params["tag"] = tag

    # Without a valid token the endpoint 302s to the sales page rather than
    # returning an error status, so a followed redirect would parse as garbage.
    resp = requests.get(url, params=params, timeout=10, allow_redirects=False)
    if resp.is_redirect or resp.is_permanent_redirect:
        raise RuntimeError(
            f"web3.career redirected to {resp.headers.get('location')!r} — token rejected"
        )
    resp.raise_for_status()

    # The payload is a 3-element array: [usage banner, ToS banner, jobs]. It is
    # served as text/html, so resp.json() is doing the real parsing here.
    payload = resp.json()
    if not (isinstance(payload, list) and len(payload) == 3 and isinstance(payload[2], list)):
        raise RuntimeError(f"unexpected web3.career payload shape: {type(payload).__name__}")

    return payload[2]


if __name__ == "__main__":
    jobs = fetch_latest_jobs(WEB3_ENDPOINT_URL, os.getenv("WEB3_API_KEY"))
    print(f"Fetched {len(jobs)} job objects.")
    for job in jobs[:5]:
        print(f"  {job['id']}  {job['date']}  {job['company']} — {job['title'].strip()}")
