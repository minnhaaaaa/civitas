"""Bounded in-process rate limiting behind a replaceable port."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0


class RateLimiter(Protocol):
    async def acquire(self, key: str) -> RateLimitDecision: ...


class FixedWindowRateLimiter:
    """Per-principal limiter for one server process.

    Deployments with several replicas should provide the same port using a shared
    atomic store.  This implementation remains useful as a mandatory local guard.
    """

    def __init__(self, *, requests: int, window_seconds: int) -> None:
        if requests < 1 or window_seconds < 1:
            raise ValueError("rate limit requests and window must be positive")
        self._requests = requests
        self._window_seconds = window_seconds
        self._windows: dict[str, tuple[float, int]] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, key: str) -> RateLimitDecision:
        now = monotonic()
        async with self._lock:
            started_at, count = self._windows.get(key, (now, 0))
            elapsed = now - started_at
            if elapsed >= self._window_seconds:
                started_at, count = now, 0
            if count >= self._requests:
                retry_after = max(1, int(self._window_seconds - elapsed + 0.999))
                return RateLimitDecision(False, retry_after)
            self._windows[key] = (started_at, count + 1)
            return RateLimitDecision(True)
