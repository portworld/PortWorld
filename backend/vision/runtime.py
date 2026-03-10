from __future__ import annotations

import asyncio
import logging
import math
from collections import deque
from dataclasses import dataclass, field

from backend.core.settings import Settings
from backend.core.storage import BackendStorage, SessionStorageResult, now_ms
from backend.memory.materializer import (
    build_accepted_vision_event,
    build_session_memory_rollup,
    build_short_term_memory,
)
from backend.vision.contracts import VisionAnalyzer, VisionFrameContext, VisionObservation
from backend.vision.factory import build_vision_analyzer
from backend.vision.gating import (
    AcceptedFrameReference,
    VisionGateError,
    VisionProviderBudgetState,
    VisionRouteDecision,
    VisionSignalSnapshot,
    decide_vision_route,
    extract_vision_signal_snapshot,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PendingVisionFrame:
    image_bytes: bytes
    frame_context: VisionFrameContext
    image_media_type: str = "image/jpeg"


@dataclass(frozen=True, slots=True)
class RouteRecord:
    frame_id: str
    capture_ts_ms: int
    action: str
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
    pending_session_events: list[dict[str, object]] = field(default_factory=list)
    last_session_rollup_at_ms: int | None = None
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


@dataclass(slots=True)
class VisionMemoryRuntime:
    settings: Settings
    storage: BackendStorage
    analyzer: VisionAnalyzer
    provider_budget: VisionBudgetManager
    started: bool = field(default=False, init=False, repr=False, compare=False)
    _workers: dict[str, SessionVisionWorker] = field(default_factory=dict, init=False, repr=False)
    _workers_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _shutdown_requested: bool = field(default=False, init=False, repr=False)

    @classmethod
    def from_settings(cls, settings: Settings, *, storage: BackendStorage) -> "VisionMemoryRuntime":
        return cls(
            settings=settings,
            storage=storage,
            analyzer=build_vision_analyzer(settings=settings),
            provider_budget=VisionBudgetManager(
                max_rps=settings.vision_provider_max_rps,
                backoff_initial_seconds=settings.vision_provider_backoff_initial_seconds,
                backoff_max_seconds=settings.vision_provider_backoff_max_seconds,
            ),
        )

    async def startup(self) -> None:
        await self.analyzer.startup()
        self._shutdown_requested = False
        self.started = True

    async def shutdown(self) -> None:
        async with self._workers_lock:
            session_ids = list(self._workers.keys())
        for session_id in session_ids:
            await self.finalize_session(session_id=session_id)
        self._shutdown_requested = True
        await self.analyzer.shutdown()
        self.started = False

    @property
    def enabled(self) -> bool:
        return True

    @property
    def provider_name(self) -> str:
        return self.analyzer.provider_name

    @property
    def model_name(self) -> str:
        return self.analyzer.model_name

    async def submit_frame(
        self,
        *,
        image_bytes: bytes,
        frame_context: VisionFrameContext,
        image_media_type: str = "image/jpeg",
    ) -> None:
        if not self.started:
            raise RuntimeError("VisionMemoryRuntime has not been started")

        worker = await self._ensure_worker(session_id=frame_context.session_id)
        incoming_frame = PendingVisionFrame(
            image_bytes=image_bytes,
            frame_context=frame_context,
            image_media_type=image_media_type,
        )
        async with worker.condition:
            if worker.close_requested:
                logger.info(
                    "VISION_SUBMIT_IGNORED_CLOSING session=%s frame=%s",
                    frame_context.session_id,
                    frame_context.frame_id,
                )
                return
            dropped_frame_id = (
                worker.latest_inbox_frame.frame_context.frame_id
                if worker.latest_inbox_frame is not None
                else None
            )
            worker.latest_inbox_frame = incoming_frame
            worker.condition.notify_all()
        if dropped_frame_id is not None:
            self.storage.update_vision_frame_processing(
                session_id=frame_context.session_id,
                frame_id=dropped_frame_id,
                processing_status="superseded",
                gate_status="skipped",
                gate_reason="replaced_by_newer_inbox_frame",
                provider=self.provider_name,
                model=self.model_name,
                error_code=None,
            )
            logger.info(
                "VISION_INBOX_FRAME_REPLACED session=%s dropped_frame=%s new_frame=%s",
                frame_context.session_id,
                dropped_frame_id,
                frame_context.frame_id,
            )
            self._cleanup_ingest_artifacts(
                session_id=frame_context.session_id,
                frame_id=dropped_frame_id,
            )

    async def analyze_frame(
        self,
        *,
        image_bytes: bytes,
        frame_context: VisionFrameContext,
        image_media_type: str = "image/jpeg",
    ) -> VisionObservation:
        return await self.analyzer.analyze_frame(
            image_bytes=image_bytes,
            frame_context=frame_context,
            image_media_type=image_media_type,
        )

    async def finalize_session(self, *, session_id: str) -> None:
        async with self._workers_lock:
            worker = self._workers.pop(session_id, None)
        if worker is None:
            return

        async with worker.condition:
            worker.close_requested = True
            worker.condition.notify_all()

        if worker.task is not None:
            try:
                await worker.task
            except asyncio.CancelledError:
                pass

        if worker.best_deferred_candidate is not None:
            deferred = worker.best_deferred_candidate
            self._mark_store_only(
                pending_frame=deferred.pending_frame,
                signal=deferred.signal,
                reason="session_finalized_before_analysis",
            )
            worker.best_deferred_candidate = None
        if worker.pending_session_events:
            self._materialize_session_memory(worker)

    async def _ensure_worker(self, *, session_id: str) -> SessionVisionWorker:
        async with self._workers_lock:
            worker = self._workers.get(session_id)
            if worker is None:
                session_storage = self.storage.ensure_session_storage(session_id=session_id)
                previous_session_memory = self.storage.read_session_memory(session_id=session_id)
                previous_short_term_memory = self.storage.read_short_term_memory(session_id=session_id)
                session_updated_at_ms = _coerce_optional_int(previous_session_memory.get("updated_at_ms"))
                short_term_updated_at_ms = _coerce_positive_optional_int(
                    previous_short_term_memory.get("window_end_ts_ms")
                )
                worker = SessionVisionWorker(
                    session_id=session_id,
                    session_storage=session_storage,
                    last_successful_analysis_at_ms=short_term_updated_at_ms,
                    short_term_memory_last_updated_at_ms=short_term_updated_at_ms,
                    session_memory_last_updated_at_ms=session_updated_at_ms,
                    last_session_rollup_at_ms=session_updated_at_ms,
                )
                worker.task = asyncio.create_task(
                    self._run_session_worker(worker),
                    name=f"vision-session-{session_id}",
                )
                self._workers[session_id] = worker
            return worker

    async def _run_session_worker(self, worker: SessionVisionWorker) -> None:
        try:
            while not self._shutdown_requested:
                work_item = await self._wait_for_work_item(worker)
                if work_item is None:
                    if self._shutdown_requested or worker.close_requested:
                        break
                    continue
                kind, payload = work_item
                if kind == "inbox":
                    await self._process_inbox_frame(worker, payload)
                else:
                    await self._process_deferred_candidate(worker, payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("VISION_SESSION_WORKER_FAILED session=%s", worker.session_id)

    async def _wait_for_work_item(
        self,
        worker: SessionVisionWorker,
    ) -> tuple[str, PendingVisionFrame | DeferredVisionCandidate] | None:
        async with worker.condition:
            while True:
                if worker.latest_inbox_frame is not None:
                    pending = worker.latest_inbox_frame
                    worker.latest_inbox_frame = None
                    return "inbox", pending
                if self._shutdown_requested or worker.close_requested:
                    return None
                if worker.best_deferred_candidate is not None:
                    deferred = worker.best_deferred_candidate
                    budget_state = await self.provider_budget.get_state()
                    now_ts_ms = now_ms()
                    deferred_ttl_ms = self.settings.vision_deferred_candidate_ttl_seconds * 1000
                    expires_at_ms = deferred.deferred_at_ms + deferred_ttl_ms
                    wake_at_ms = min(expires_at_ms, budget_state.available_at_ms)
                    if wake_at_ms <= now_ts_ms:
                        return "deferred", deferred
                    try:
                        await asyncio.wait_for(
                            worker.condition.wait(),
                            timeout=(wake_at_ms - now_ts_ms) / 1000.0,
                        )
                    except asyncio.TimeoutError:
                        return "deferred", deferred
                    continue
                await worker.condition.wait()

    async def _process_inbox_frame(
        self,
        worker: SessionVisionWorker,
        pending_frame: PendingVisionFrame,
    ) -> None:
        budget_state = await self.provider_budget.get_state()
        try:
            signal = self._build_signal_snapshot(
                worker=worker,
                pending_frame=pending_frame,
                provider_budget_state=budget_state,
            )
        except VisionGateError:
            self.storage.update_vision_frame_processing(
                session_id=pending_frame.frame_context.session_id,
                frame_id=pending_frame.frame_context.frame_id,
                processing_status="gate_failed",
                gate_status="error",
                gate_reason="image_decode_failed",
                provider=self.provider_name,
                model=self.model_name,
                analyzed_at_ms=now_ms(),
                error_code="VISION_GATE_FAILED",
            )
            worker.last_analysis_failed = True
            logger.exception(
                "VISION_SIGNAL_EXTRACTION_FAILED session=%s frame=%s",
                pending_frame.frame_context.session_id,
                pending_frame.frame_context.frame_id,
            )
            self._cleanup_ingest_artifacts(
                session_id=pending_frame.frame_context.session_id,
                frame_id=pending_frame.frame_context.frame_id,
            )
            return

        route = decide_vision_route(
            signal=signal,
            min_analysis_gap_seconds=self.settings.vision_min_analysis_gap_seconds,
            scene_change_hamming_threshold=self.settings.vision_scene_change_hamming_threshold,
            analysis_heartbeat_seconds=self.settings.vision_analysis_heartbeat_seconds,
        )
        worker.latest_route_records.append(
            RouteRecord(
                frame_id=signal.frame_id,
                capture_ts_ms=signal.capture_ts_ms,
                action=route.action,
                reason=route.reason,
                priority_score=route.priority_score,
                novelty_score=route.novelty_score,
                freshness_score=route.freshness_score,
                dhash_hex=signal.dhash_hex,
                hamming_distance=signal.hamming_distance,
            )
        )
        logger.info(
            "VISION_ROUTE_DECISION session=%s frame=%s action=%s reason=%s priority=%.3f novelty=%.3f freshness=%.3f provider_available=%s provider_reason=%s",
            signal.session_id,
            signal.frame_id,
            route.action,
            route.reason,
            route.priority_score,
            route.novelty_score,
            route.freshness_score,
            signal.provider_available_now,
            signal.provider_budget_reason,
        )
        if route.action == "drop_redundant":
            self._mark_drop_redundant(
                pending_frame=pending_frame,
                signal=signal,
                reason=route.reason,
            )
            return
        if route.action == "store_only":
            self._mark_store_only(
                pending_frame=pending_frame,
                signal=signal,
                reason=route.reason,
            )
            return
        if route.action == "defer_candidate":
            await self._defer_candidate(
                worker=worker,
                pending_frame=pending_frame,
                signal=signal,
                route=route,
            )
            return
        await self._analyze_now(
            worker=worker,
            pending_frame=pending_frame,
            signal=signal,
            route=route,
        )

    async def _process_deferred_candidate(
        self,
        worker: SessionVisionWorker,
        deferred: DeferredVisionCandidate,
    ) -> None:
        if worker.best_deferred_candidate is not deferred:
            return
        deferred_ttl_ms = self.settings.vision_deferred_candidate_ttl_seconds * 1000
        now_ts_ms = now_ms()
        expires_at_ms = deferred.deferred_at_ms + deferred_ttl_ms
        if now_ts_ms >= expires_at_ms:
            self._mark_store_only(
                pending_frame=deferred.pending_frame,
                signal=deferred.signal,
                reason="deferred_candidate_expired",
            )
            worker.best_deferred_candidate = None
            return

        budget_state = await self.provider_budget.get_state()
        try:
            signal = self._build_signal_snapshot(
                worker=worker,
                pending_frame=deferred.pending_frame,
                provider_budget_state=budget_state,
            )
        except VisionGateError:
            self.storage.update_vision_frame_processing(
                session_id=deferred.pending_frame.frame_context.session_id,
                frame_id=deferred.pending_frame.frame_context.frame_id,
                processing_status="gate_failed",
                gate_status="error",
                gate_reason="image_decode_failed",
                provider=self.provider_name,
                model=self.model_name,
                analyzed_at_ms=now_ms(),
                error_code="VISION_GATE_FAILED",
            )
            worker.best_deferred_candidate = None
            worker.last_analysis_failed = True
            self._cleanup_ingest_artifacts(
                session_id=deferred.pending_frame.frame_context.session_id,
                frame_id=deferred.pending_frame.frame_context.frame_id,
            )
            return

        route = decide_vision_route(
            signal=signal,
            min_analysis_gap_seconds=self.settings.vision_min_analysis_gap_seconds,
            scene_change_hamming_threshold=self.settings.vision_scene_change_hamming_threshold,
            analysis_heartbeat_seconds=self.settings.vision_analysis_heartbeat_seconds,
        )
        if route.action == "defer_candidate":
            return
        worker.best_deferred_candidate = None
        if route.action == "drop_redundant":
            self._mark_drop_redundant(
                pending_frame=deferred.pending_frame,
                signal=signal,
                reason=route.reason,
            )
            return
        if route.action == "store_only":
            self._mark_store_only(
                pending_frame=deferred.pending_frame,
                signal=signal,
                reason=route.reason,
            )
            return
        await self._analyze_now(
            worker=worker,
            pending_frame=deferred.pending_frame,
            signal=signal,
            route=route,
        )

    def _build_signal_snapshot(
        self,
        *,
        worker: SessionVisionWorker,
        pending_frame: PendingVisionFrame,
        provider_budget_state: VisionProviderBudgetState,
    ) -> VisionSignalSnapshot:
        short_term_age_ms = _compute_age_ms(
            current_capture_ts_ms=pending_frame.frame_context.capture_ts_ms,
            memory_ts_ms=worker.short_term_memory_last_updated_at_ms,
        )
        session_age_ms = _compute_age_ms(
            current_capture_ts_ms=pending_frame.frame_context.capture_ts_ms,
            memory_ts_ms=worker.session_memory_last_updated_at_ms,
        )
        return extract_vision_signal_snapshot(
            image_bytes=pending_frame.image_bytes,
            frame_context=pending_frame.frame_context,
            last_accepted_frame=worker.last_accepted_frame,
            has_short_term_memory=worker.short_term_memory_last_updated_at_ms is not None,
            has_session_memory=worker.session_memory_last_updated_at_ms is not None,
            short_term_memory_age_ms=short_term_age_ms,
            session_memory_age_ms=session_age_ms,
            last_successful_analysis_at_ms=worker.last_successful_analysis_at_ms,
            last_analysis_failed=worker.last_analysis_failed,
            provider_budget_state=provider_budget_state,
        )

    async def _defer_candidate(
        self,
        *,
        worker: SessionVisionWorker,
        pending_frame: PendingVisionFrame,
        signal: VisionSignalSnapshot,
        route: VisionRouteDecision,
    ) -> None:
        incoming = DeferredVisionCandidate(
            pending_frame=pending_frame,
            signal=signal,
            route=route,
            deferred_at_ms=now_ms(),
        )
        existing = worker.best_deferred_candidate
        if existing is None:
            worker.best_deferred_candidate = incoming
            self.storage.update_vision_frame_processing(
                session_id=signal.session_id,
                frame_id=signal.frame_id,
                processing_status="deferred",
                gate_status="accepted",
                gate_reason=route.reason,
                phash=signal.dhash_hex,
                provider=self.provider_name,
                model=self.model_name,
            )
            async with worker.condition:
                worker.condition.notify_all()
            return
        if _is_candidate_stronger(incoming, existing):
            worker.best_deferred_candidate = incoming
            self.storage.update_vision_frame_processing(
                session_id=signal.session_id,
                frame_id=signal.frame_id,
                processing_status="deferred",
                gate_status="accepted",
                gate_reason=route.reason,
                phash=signal.dhash_hex,
                provider=self.provider_name,
                model=self.model_name,
            )
            self._mark_store_only(
                pending_frame=existing.pending_frame,
                signal=existing.signal,
                reason="deferred_replaced_by_higher_priority_candidate",
            )
            async with worker.condition:
                worker.condition.notify_all()
            return
        self._mark_store_only(
            pending_frame=pending_frame,
            signal=signal,
            reason="deferred_not_selected_lower_priority",
        )

    async def _analyze_now(
        self,
        *,
        worker: SessionVisionWorker,
        pending_frame: PendingVisionFrame,
        signal: VisionSignalSnapshot,
        route: VisionRouteDecision,
    ) -> None:
        slot_state = await self.provider_budget.acquire_analysis_slot()
        if not slot_state.available_now:
            await self._defer_candidate(
                worker=worker,
                pending_frame=pending_frame,
                signal=signal,
                route=VisionRouteDecision(
                    session_id=route.session_id,
                    frame_id=route.frame_id,
                    action="defer_candidate",
                    reason="provider_budget_unavailable_after_acquire",
                    priority_score=route.priority_score,
                    novelty_score=route.novelty_score,
                    freshness_score=route.freshness_score,
                    memory_bootstrap_required=route.memory_bootstrap_required,
                    provider_budget_available=False,
                    provider_cooldown_until_ms=slot_state.cooldown_until_ms,
                ),
            )
            return

        self.storage.update_vision_frame_processing(
            session_id=pending_frame.frame_context.session_id,
            frame_id=pending_frame.frame_context.frame_id,
            processing_status="analyzing",
            gate_status="accepted",
            gate_reason=route.reason,
            phash=signal.dhash_hex,
            provider=self.provider_name,
            model=self.model_name,
        )
        try:
            observation = await self.analyzer.analyze_frame(
                image_bytes=pending_frame.image_bytes,
                frame_context=pending_frame.frame_context,
                image_media_type=pending_frame.image_media_type,
            )
        except Exception:
            await self.provider_budget.record_non_rate_limit_failure()
            self.storage.update_vision_frame_processing(
                session_id=pending_frame.frame_context.session_id,
                frame_id=pending_frame.frame_context.frame_id,
                processing_status="analysis_failed",
                gate_status="accepted",
                gate_reason=route.reason,
                phash=signal.dhash_hex,
                provider=self.provider_name,
                model=self.model_name,
                analyzed_at_ms=now_ms(),
                error_code="VISION_ANALYSIS_FAILED",
            )
            worker.last_analysis_failed = True
            logger.exception(
                "VISION_ANALYSIS_FAILED session=%s frame=%s provider=%s model=%s",
                pending_frame.frame_context.session_id,
                pending_frame.frame_context.frame_id,
                self.provider_name,
                self.model_name,
            )
            self._cleanup_ingest_artifacts(
                session_id=pending_frame.frame_context.session_id,
                frame_id=pending_frame.frame_context.frame_id,
            )
            return

        await self.provider_budget.record_success()
        worker.last_analysis_failed = False
        worker.last_successful_analysis_at_ms = pending_frame.frame_context.capture_ts_ms
        worker.last_accepted_frame = AcceptedFrameReference(
            capture_ts_ms=pending_frame.frame_context.capture_ts_ms,
            dhash_hex=signal.dhash_hex,
        )
        worker.last_observation = observation
        accepted_event = build_accepted_vision_event(
            observation=observation,
            provider=self.provider_name,
            model=self.model_name,
        )
        self.storage.append_vision_event(
            session_id=observation.session_id,
            event=accepted_event,
        )
        worker.pending_session_events.append(accepted_event)
        self._materialize_short_term_memory(worker)
        if self._should_roll_session_memory(worker):
            self._materialize_session_memory(worker)
        self.storage.update_vision_frame_processing(
            session_id=observation.session_id,
            frame_id=observation.frame_id,
            processing_status="analyzed",
            gate_status="accepted",
            gate_reason=route.reason,
            phash=signal.dhash_hex,
            provider=self.provider_name,
            model=self.model_name,
            analyzed_at_ms=now_ms(),
            summary_snippet=observation.scene_summary[:240],
        )
        logger.info(
            "VISION_ANALYSIS_ACCEPTED session=%s frame=%s provider=%s model=%s scene_summary=%s",
            observation.session_id,
            observation.frame_id,
            self.provider_name,
            self.model_name,
            observation.scene_summary,
        )
        self._cleanup_ingest_artifacts(
            session_id=observation.session_id,
            frame_id=observation.frame_id,
        )

    def _mark_drop_redundant(
        self,
        *,
        pending_frame: PendingVisionFrame,
        signal: VisionSignalSnapshot,
        reason: str,
    ) -> None:
        self.storage.update_vision_frame_processing(
            session_id=pending_frame.frame_context.session_id,
            frame_id=pending_frame.frame_context.frame_id,
            processing_status="gated_rejected",
            gate_status="rejected",
            gate_reason=reason,
            phash=signal.dhash_hex,
            provider=self.provider_name,
            model=self.model_name,
        )
        self._cleanup_ingest_artifacts(
            session_id=pending_frame.frame_context.session_id,
            frame_id=pending_frame.frame_context.frame_id,
        )

    def _mark_store_only(
        self,
        *,
        pending_frame: PendingVisionFrame,
        signal: VisionSignalSnapshot,
        reason: str,
    ) -> None:
        self.storage.update_vision_frame_processing(
            session_id=pending_frame.frame_context.session_id,
            frame_id=pending_frame.frame_context.frame_id,
            processing_status="stored_only",
            gate_status="accepted",
            gate_reason=reason,
            phash=signal.dhash_hex,
            provider=self.provider_name,
            model=self.model_name,
        )
        self._cleanup_ingest_artifacts(
            session_id=pending_frame.frame_context.session_id,
            frame_id=pending_frame.frame_context.frame_id,
        )

    def _materialize_short_term_memory(self, worker: SessionVisionWorker) -> None:
        accepted_events = self.storage.read_vision_events(session_id=worker.session_id)
        payload, markdown_text = build_short_term_memory(
            session_id=worker.session_id,
            accepted_events=accepted_events,
            window_seconds=self.settings.vision_short_term_window_seconds,
        )
        self.storage.write_short_term_memory(
            session_id=worker.session_id,
            payload=payload,
            markdown_text=markdown_text,
        )
        worker.short_term_memory_last_updated_at_ms = _coerce_optional_int(payload.get("window_end_ts_ms"))

    def _should_roll_session_memory(self, worker: SessionVisionWorker) -> bool:
        if not worker.pending_session_events:
            return False
        if worker.last_session_rollup_at_ms is None:
            return True
        if len(worker.pending_session_events) >= self.settings.vision_session_rollup_min_accepted_events:
            return True
        elapsed_ms = now_ms() - worker.last_session_rollup_at_ms
        return elapsed_ms >= self.settings.vision_session_rollup_interval_seconds * 1000

    def _materialize_session_memory(self, worker: SessionVisionWorker) -> None:
        previous_memory = self.storage.read_session_memory(session_id=worker.session_id)
        payload, markdown_text = build_session_memory_rollup(
            session_id=worker.session_id,
            previous_memory=previous_memory,
            recent_events=list(worker.pending_session_events),
        )
        self.storage.write_session_memory(
            session_id=worker.session_id,
            payload=payload,
            markdown_text=markdown_text,
        )
        worker.pending_session_events.clear()
        worker.last_session_rollup_at_ms = int(payload["updated_at_ms"])
        worker.session_memory_last_updated_at_ms = int(payload["updated_at_ms"])

    def _cleanup_ingest_artifacts(self, *, session_id: str, frame_id: str) -> None:
        if self.settings.vision_debug_retain_raw_frames:
            return
        self.storage.delete_vision_ingest_artifacts(
            session_id=session_id,
            frame_id=frame_id,
        )


def _compute_age_ms(*, current_capture_ts_ms: int, memory_ts_ms: int | None) -> int | None:
    if memory_ts_ms is None:
        return None
    if current_capture_ts_ms <= memory_ts_ms:
        return 0
    return current_capture_ts_ms - memory_ts_ms


def _is_candidate_stronger(
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


def _coerce_optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_positive_optional_int(value: object) -> int | None:
    parsed = _coerce_optional_int(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed
