import secrets

from fastapi import HTTPException, Request


class ApiKey:
    # Shared-key guard on /ask. When the key is unset the endpoint stays open
    # (local dev + CI); when set, every request must present it as
    # `Authorization: Bearer <key>`. compare_digest keeps the check constant-time
    # so the key length can't be timed out of the response.
    def __init__(self, key: str | None = None) -> None:
        self._key = key

    def __call__(self, request: Request) -> None:
        if not self._key:
            return
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(token, self._key):
            raise HTTPException(status_code=401, detail="Missing or invalid API key")
