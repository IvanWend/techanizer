import requests

REMOTIVE_ENDPOINT_URL = "https://remotive.com/api/remote-jobs"


def fetch_latest_jobs(url: str) -> list[dict]:
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    jobs: list[dict] = data.get("jobs", [])
    return jobs


if __name__ == "__main__":
    job_objects = fetch_latest_jobs(REMOTIVE_ENDPOINT_URL)
    print(f"Fetched {len(job_objects)} job objects.")
