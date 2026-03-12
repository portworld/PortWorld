from __future__ import annotations

import asyncio
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from backend.core.storage import SessionStorageResult, now_ms
from backend.vision.contracts import VisionFrameContext, VisionObservation
from backend.vision.policy.gating import (
    AcceptedFrameReference,
    VisionRouteAction,
    VisionProviderBudgetState,
    VisionRouteDecision,
    VisionSignalSnapshot,
)

BootstrapState: TypeAlias = Literal[
    "unbootstrapped",
    "bootstrap_pending",
    "bootstrapped",
    "bootstrap_degraded",
]


@dataclass(frozen=True, slots=True)
class PendingVisionFrame:
    image_bytes: bytes
    frame_context: VisionFrameContext
    image_media_type: str = "image/jpeg"


@dataclass(frozen=True, slots=True)
class RouteRecord:
    frame_id: str
    capture_ts_ms: int
    action: VisionRouteAction
    reason: str
    priority_score: float
    novelty_score: float
    freshness_score: float
    dhash_hex: str
    hamming_distance: int | None


@dataclass(frozen=True, slots=True)
class DeferredVisionCandidate:
    pending_frame: PendingVisionFrame
    signal: VisionSignalSnapshot
    route: VisionRouteDecision
    deferred_at_ms: int
    bootstrap_candidate: bool = False


@dataclass(slots=True)
class SessionVisionWorker:
    session_id: str
    session_storage: SessionStorageResult
    latest_inbox_frame: PendingVisionFrame | None = None
    best_deferred_candidate: DeferredVisionCandidate | None = None
    latest_route_records: deque[RouteRecord] = field(default_factory=lambda: deque(maxlen=25))
    last_accepted_frame: AcceptedFrameReference | None = None
    last_observation: VisionObservation | None = None
    last_successful_analysis_at_ms: int | None = None
    last_analysis_failed: bool = False
    short_term_memory_last_updated_at_ms: int | None = None
    session_memory_last_updated_at_ms: int | None = None
    session_memory_exists: bool = False
    accepted_event_count: int = 0
    bootstrap_state: BootstrapState = "unbootstrapped"
    pending_session_events: list[dict[str, object]] = field(default_factory=list)
    short_term_window_events: deque[dict[str, object]] = field(default_factory=deque)
    last_session_rollup_at_ms: int | None = None
    last_worker_error: str | None = None
    close_requested: bool = False
    task: asyncio.Task[None] | None = None
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)


class VisionBudgetManager:
    def __init__(
        self,
        *,
        max_rps: int,
        backoff_initial_seconds: int,
        backoff_max_seconds: int,
    ) -> None:
        self._max_rps = max(1, max_rps)
        self._backoff_initial_seconds = max(1, backoff_initial_seconds)
        self._backoff_max_seconds = max(1, backoff_max_seconds)
        self._min_interval_ms = max(1, int(math.ceil(1000 / self._max_rps)))
        self._next_slot_at_ms = 0
        self._cooldown_until_ms: int | None = None
        self._consecutive_429_streak = 0
        self._lock = asyncio.Lock()

    async def get_state(self) -> VisionProviderBudgetState:
        async with self._lock:
            return self._build_state_locked(now_ts_ms=now_ms())

    async def is_available_now(self) -> bool:
        state = await self.get_state()
        return state.available_now

    async def acquire_analysis_slot(self) -> VisionProviderBudgetState:
        async with self._lock:
            now_ts_ms = now_ms()
            state = self._build_state_locked(now_ts_ms=now_ts_ms)
            if not state.available_now:
                return state
            self._next_slot_at_ms = now_ts_ms + self._min_interval_ms
            return VisionProviderBudgetState(
                available_now=True,
                available_at_ms=now_ts_ms,
                cooldown_until_ms=None,
                consecutive_rate_limit_count=self._consecutive_429_streak,
                reason="acquired_slot",
            )

    async def record_success(self) -> None:
        async with self._lock:
            self._consecutive_429_streak = 0

    async def record_rate_limit(self, retry_after_seconds: float | None = None) -> None:
        async with self._lock:
            self._consecutive_429_streak += 1
            if retry_after_seconds is not None and retry_after_seconds > 0:
                cooldown_seconds = max(1, int(math.ceil(retry_after_seconds)))
            else:
                exponent = max(0, self._consecutive_429_streak - 1)
                cooldown_seconds = min(
                    self._backoff_max_seconds,
                    self._backoff_initial_seconds * (2**exponent),
                )
            until_ms = now_ms() + (cooldown_seconds * 1000)
            if self._cooldown_until_ms is None:
                self._cooldown_until_ms = until_ms
            else:
                self._cooldown_until_ms = max(self._cooldown_until_ms, until_ms)

    async def record_non_rate_limit_failure(self) -> None:
        async with self._lock:
            self._consecutive_429_streak = 0

    def _build_state_locked(self, *, now_ts_ms: int) -> VisionProviderBudgetState:
        cooldown_until_ms = self._cooldown_until_ms
        if cooldown_until_ms is not None and now_ts_ms >= cooldown_until_ms:
            self._cooldown_until_ms = None
            cooldown_until_ms = None
        if cooldown_until_ms is not None and now_ts_ms < cooldown_until_ms:
            return VisionProviderBudgetState(
                available_now=False,
                available_at_ms=max(cooldown_until_ms, self._next_slot_at_ms),
                cooldown_until_ms=cooldown_until_ms,
                consecutive_rate_limit_count=self._consecutive_429_streak,
                reason="cooldown_active",
            )
        if now_ts_ms < self._next_slot_at_ms:
            return VisionProviderBudgetState(
                available_now=False,
                available_at_ms=self._next_slot_at_ms,
                cooldown_until_ms=None,
                consecutive_rate_limit_count=self._consecutive_429_streak,
                reason="rps_limit",
            )
        return VisionProviderBudgetState(
            available_now=True,
            available_at_ms=now_ts_ms,
            cooldown_until_ms=None,
            consecutive_rate_limit_count=self._consecutive_429_streak,
            reason="available",
        )


def compute_age_ms(*, current_capture_ts_ms: int, memory_ts_ms: int | None) -> int | None:
    if memory_ts_ms is None:
        return None
    if current_capture_ts_ms <= memory_ts_ms:
        return 0
    return current_capture_ts_ms - memory_ts_ms


def latest_capture_ts_from_events(events: list[dict[str, object]]) -> int | None:
    latest: int | None = None
    for event in events:
        capture_ts_ms = coerce_optional_int(event.get("capture_ts_ms"))
        if capture_ts_ms is None:
            continue
        if latest is None or capture_ts_ms > latest:
            latest = capture_ts_ms
    return latest


def is_candidate_stronger(
    incoming: DeferredVisionCandidate,
    existing: DeferredVisionCandidate,
) -> bool:
    if incoming.route.priority_score > existing.route.priority_score:
        return True
    if incoming.route.priority_score < existing.route.priority_score:
        return False
    return (
        incoming.pending_frame.frame_context.capture_ts_ms
        > existing.pending_frame.frame_context.capture_ts_ms
    )


def build_budget_state_from_signal(signal: VisionSignalSnapshot) -> VisionProviderBudgetState:
    available_at_ms = signal.capture_ts_ms
    if signal.provider_cooldown_until_ms is not None:
        available_at_ms = signal.provider_cooldown_until_ms
    return VisionProviderBudgetState(
        available_now=signal.provider_available_now,
        available_at_ms=available_at_ms,
        cooldown_until_ms=signal.provider_cooldown_until_ms,
        consecutive_rate_limit_count=0,
        reason=signal.provider_budget_reason,
    )


def coerce_optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def coerce_positive_optional_int(value: object) -> int | None:
    parsed = coerce_optional_int(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed
