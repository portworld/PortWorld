from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass
from math import ceil
from time import monotonic
from typing import Deque


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0
    scope: str = "unknown"


class SlidingWindowRateLimiter:
    """In-memory per-process sliding-window limiter."""

    def __init__(self) -> None:
        self._events: dict[str, Deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def allow(
        self,
        *,
        key: str,
        limit: int,
        window_seconds: int,
        scope: str,
    ) -> RateLimitDecision:
        if limit <= 0:
            return RateLimitDecision(allowed=True, scope=scope)
        now = monotonic()
        oldest_allowed = now - window_seconds
        async with self._lock:
            bucket = self._events[key]
            while bucket and bucket[0] <= oldest_allowed:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after_seconds = max(1, int(ceil((bucket[0] + window_seconds) - now)))
                return RateLimitDecision(
                    allowed=False,
                    retry_after_seconds=retry_after_seconds,
                    scope=scope,
                )
            bucket.append(now)
        return RateLimitDecision(allowed=True, scope=scope)
