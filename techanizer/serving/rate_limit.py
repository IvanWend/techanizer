import threading
import time
from collections.abc import Callable

from fastapi import HTTPException, Request


class RateLimiter:
    # Fixed-window counter keyed by client IP, kept in-process. One uvicorn
    # worker on one VPS, so in-process is enough; it resets on restart. The
    # window bookkeeping sits under a lock because sync endpoints run on a
    # threadpool.
    def __init__(
        self,
        max_requests: int,
        window_seconds: float,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_requests = max_requests
        self._window = window_seconds
        self._now = now
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[float, int]] = {}

    def __call__(self, request: Request) -> None:
        ip = request.client.host if request.client is not None else "unknown"
        now = self._now()
        with self._lock:
            # Drop expired windows so distinct-IP churn can't grow the dict.
            expired = [k for k, (start, _) in self._windows.items() if now - start >= self._window]
            for k in expired:
                del self._windows[k]
            start, count = self._windows.get(ip, (now, 0))
            if now - start >= self._window:
                start, count = now, 0
            count += 1
            self._windows[ip] = (start, count)
        if count > self._max_requests:
            retry_after = int(self._window - (now - start)) + 1
            raise HTTPException(
                status_code=429,
                detail="Too many requests",
                headers={"Retry-After": str(retry_after)},
            )
