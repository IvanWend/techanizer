from datetime import UTC, datetime, timedelta

# The rolling freshness window. With retention now keep-everything (011), the
# window is enforced at two layers: load.py drops rows that are already aged-out
# when first fetched, and the live agent scopes its queries to this window via
# db_meta.role='live' (storage/analytics.py + vector_search.py).
WINDOW_DAYS = 90


def window_cutoff(now: datetime | None = None) -> datetime:
    return (now or datetime.now(UTC)) - timedelta(days=WINDOW_DAYS)
