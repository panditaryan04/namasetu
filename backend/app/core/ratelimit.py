"""Sliding-window rate limiting per route group, keyed by signed-in user or else by client IP.

Requests carrying a valid access token are counted per user, so people sharing one IP (a hospital NAT, or
every browser behind the frontend's nginx proxy) do not use up each other's budget. Anonymous requests,
including sign-in, are counted per client IP; behind a proxy, uvicorn must trust it (FORWARDED_ALLOW_IPS)
to see that IP.

In-process (per API worker). Behind several workers or replicas, put the limiter in the gateway or back it
with Redis; the interface stays the same.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from app.core.security import TokenError, decode_token

LIMITS: dict[str, tuple[int, float]] = {
    "auth": (10, 60.0),  # password login / ABHA token: 10 per minute (credential guessing)
    "refresh": (60, 60.0),  # token refresh: every full page load refreshes once, so allow normal browsing
    "map": (120, 60.0),
    "search": (600, 60.0),
    "heavy": (6, 60.0),  # evaluation runs and export jobs
}


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, group: str, key: str) -> float | None:
        """Record a hit; return seconds to wait if the limit is exceeded, else None."""
        limit, window = LIMITS[group]
        now = time.monotonic()
        with self._lock:
            q = self._hits[(group, key)]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return max(0.0, window - (now - q[0]))
            q.append(now)
            return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def client_key(request: Request) -> str:
    """`user:<sub>` for a valid access token, else `ip:<address>`."""
    auth = request.headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        try:
            return f"user:{decode_token(auth[7:].strip(), 'access')['sub']}"
        except TokenError:
            pass  # invalid or expired: count it against the IP like any anonymous request
    return f"ip:{request.client.host if request.client else 'unknown'}"


def rate_limit(group: str):  # type: ignore[no-untyped-def]
    def dependency(request: Request) -> None:
        wait = limiter.hit(group, client_key(request))
        if wait is not None:
            raise HTTPException(
                status_code=429,
                detail=f"rate limit exceeded for {group}; retry in {int(wait) + 1}s",
                headers={"Retry-After": str(int(wait) + 1)},
            )

    return dependency
