"""Abuse limits for the unauthenticated auth surface.

The middleware limiter in `main.py` is a broad per-IP ceiling (hundreds a minute) — far too loose
for endpoints that create organizations or send mail to an address the caller doesn't own. These
are the tight, per-endpoint limits:

- **per IP** — how often one caller may hit an endpoint at all.
- **per address** — how often *any* caller may cause mail to a given address, so `forgot` and
  `verify/resend` can't be turned into a mail bomb aimed at someone else's inbox.

In-process, like the middleware limiter: correct for a single worker, approximate across several.
Front it with a shared store (Redis) before running multiple processes.
"""

import time
from collections.abc import Callable
from hashlib import sha256

from fastapi import HTTPException, Request

from app.core.config import Settings, get_settings

# (scope, key) -> (window start, count)
_BUCKETS: dict[tuple[str, str], tuple[float, int]] = {}
_MAX_BUCKETS = 10_000  # bounded so a flood of distinct keys can't grow this without limit


def reset() -> None:
    """Drop all counters (tests)."""
    _BUCKETS.clear()


def _consume(scope: str, key: str, *, limit: int, window_s: float) -> float:
    """Count one hit. Returns seconds to wait if the limit is spent, else 0."""
    now = time.monotonic()
    start, count = _BUCKETS.get((scope, key), (now, 0))
    if now - start >= window_s:
        start, count = now, 0
    count += 1
    if len(_BUCKETS) >= _MAX_BUCKETS and (scope, key) not in _BUCKETS:
        _BUCKETS.clear()  # crude, but keeps a spray of unique keys from exhausting memory
    _BUCKETS[(scope, key)] = (start, count)
    return 0.0 if count <= limit else start + window_s - now


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


class AuthLimit:
    """FastAPI dependency: at most `limit(settings)` requests per `window_s` from one client IP.

    The ceiling is read from settings on each call rather than captured at import, so it stays
    configurable (and testable) instead of freezing whatever the process booted with.
    """

    def __init__(self, scope: str, *, limit: Callable[[Settings], int], window_s: float) -> None:
        self._scope, self._limit, self._window = scope, limit, window_s

    async def __call__(self, request: Request) -> None:
        settings = get_settings()
        if not settings.auth_rate_limits_enabled:
            return
        retry_after = _consume(
            self._scope,
            _client_key(request),
            limit=self._limit(settings),
            window_s=self._window,
        )
        if retry_after:
            raise HTTPException(
                status_code=429,
                detail="too_many_requests",
                headers={"Retry-After": str(int(retry_after) + 1)},
            )


def enforce_address_cooldown(scope: str, email: str) -> None:
    """One outbound mail per address per cooldown, whoever asks.

    Raises 429 — which is safe to surface here because the caller already told us the address;
    it reveals nothing about whether an account exists.
    """
    settings = get_settings()
    if not settings.auth_rate_limits_enabled:
        return
    # Hashed so an in-memory dump isn't a list of everyone's addresses.
    key = sha256(email.strip().lower().encode()).hexdigest()
    retry_after = _consume(
        scope, key, limit=1, window_s=float(settings.auth_email_cooldown_seconds)
    )
    if retry_after:
        raise HTTPException(
            status_code=429,
            detail="too_many_requests",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )
