from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from math import ceil
from time import monotonic
from typing import TYPE_CHECKING, Deque

if TYPE_CHECKING:
    from fastapi import Request


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0
    scope: str = "unknown"


@dataclass(slots=True)
class _RateBucket:
    events: Deque[float]
    last_seen: float
    window_seconds: int


@dataclass(slots=True)
class _RateLimitShard:
    buckets: dict[str, _RateBucket]
    lock: asyncio.Lock
    last_cleanup_at: float


class SlidingWindowRateLimiter:
    """In-memory per-process sliding-window limiter."""

    def __init__(
        self,
        *,
        num_shards: int = 64,
        max_keys: int = 50_000,
        cleanup_interval_seconds: int = 30,
        min_idle_ttl_seconds: int = 300,
    ) -> None:
        shard_count = max(1, num_shards)
        self._cleanup_interval_seconds = max(1, cleanup_interval_seconds)
        self._min_idle_ttl_seconds = max(1, min_idle_ttl_seconds)
        self._max_keys_per_shard = max(1, int(ceil(max(1, max_keys) / shard_count)))
        self._shards = [
            _RateLimitShard(buckets={}, lock=asyncio.Lock(), last_cleanup_at=0.0)
            for _ in range(shard_count)
        ]

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
        shard = self._shard_for_key(key)
        async with shard.lock:
            self._cleanup_shard_if_due(shard, now)
            bucket = shard.buckets.get(key)
            if bucket is None:
                bucket = _RateBucket(events=deque(), last_seen=now, window_seconds=window_seconds)
                shard.buckets[key] = bucket
            else:
                bucket.last_seen = now
                bucket.window_seconds = window_seconds

            oldest_allowed = now - window_seconds
            self._prune_expired_events(bucket=bucket, oldest_allowed=oldest_allowed)
            if len(bucket.events) >= limit:
                retry_after_seconds = max(
                    1, int(ceil((bucket.events[0] + window_seconds) - now))
                )
                return RateLimitDecision(
                    allowed=False,
                    retry_after_seconds=retry_after_seconds,
                    scope=scope,
                )
            bucket.events.append(now)
            self._enforce_shard_key_cap(shard)
        return RateLimitDecision(allowed=True, scope=scope)

    def _cleanup_shard_if_due(self, shard: _RateLimitShard, now: float) -> None:
        if now - shard.last_cleanup_at < self._cleanup_interval_seconds:
            return
        shard.last_cleanup_at = now
        stale_keys: list[str] = []
        for key, bucket in shard.buckets.items():
            oldest_allowed = now - bucket.window_seconds
            self._prune_expired_events(bucket=bucket, oldest_allowed=oldest_allowed)
            idle_ttl = max(bucket.window_seconds * 3, self._min_idle_ttl_seconds)
            if not bucket.events and bucket.last_seen <= now - idle_ttl:
                stale_keys.append(key)
        for key in stale_keys:
            shard.buckets.pop(key, None)

    def _enforce_shard_key_cap(self, shard: _RateLimitShard) -> None:
        over_limit = len(shard.buckets) - self._max_keys_per_shard
        if over_limit <= 0:
            return
        oldest_first = sorted(shard.buckets.items(), key=lambda item: item[1].last_seen)
        for key, _ in oldest_first[:over_limit]:
            shard.buckets.pop(key, None)

    def _shard_for_key(self, key: str) -> _RateLimitShard:
        return self._shards[hash(key) % len(self._shards)]

    @staticmethod
    def _prune_expired_events(*, bucket: _RateBucket, oldest_allowed: float) -> None:
        while bucket.events and bucket.events[0] <= oldest_allowed:
            bucket.events.popleft()


async def enforce_http_rate_limit(request: "Request", endpoint: str) -> None:
    from fastapi import HTTPException

    from backend.core.http import client_ip_from_connection
    from backend.core.runtime import get_app_runtime

    runtime = get_app_runtime(request.app)
    client_ip = client_ip_from_connection(request)
    decision = await runtime.limit_http_request(client_ip=client_ip, endpoint=endpoint)
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded for {decision.scope}.",
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )
