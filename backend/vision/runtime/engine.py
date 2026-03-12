from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from backend.core.settings import Settings
from backend.core.storage import BackendStorage, now_ms
from backend.vision.contracts import (
    VisionAnalyzer,
    VisionFrameContext,
    VisionObservation,
)
from backend.vision.factory import build_vision_analyzer
from backend.vision.policy.gating import (
    VisionGateError,
    VisionProviderBudgetState,
    VisionSignalSnapshot,
    decide_vision_route,
)
from backend.vision.runtime.analysis import VisionAnalysisMixin
from backend.vision.runtime.journal import VisionFrameJournalMixin
from backend.vision.runtime.models import (
    DeferredVisionCandidate,
    PendingVisionFrame,
    RouteRecord,
    SessionVisionWorker,
    VisionBudgetManager,
    build_budget_state_from_signal,
    coerce_optional_int,
    coerce_positive_optional_int,
)
from backend.vision.runtime.projection import VisionMemoryProjectionMixin

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class VisionMemoryRuntime(
    VisionAnalysisMixin,
    VisionFrameJournalMixin,
    VisionMemoryProjectionMixin,
):
    settings: Settings
    storage: BackendStorage
    analyzer: VisionAnalyzer
    provider_budget: VisionBudgetManager
    started: bool = field(default=False, init=False, repr=False, compare=False)
    _workers: dict[str, SessionVisionWorker] = field(default_factory=dict, init=False, repr=False)
    _worker_bootstraps: dict[str, asyncio.Future[SessionVisionWorker]] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
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

    async def _run_storage(self, operation, /, *args, **kwargs):
        return await asyncio.to_thread(operation, *args, **kwargs)

    async def _update_frame_processing(
        self,
        *,
        session_id: str,
        frame_id: str,
        processing_status: str,
        gate_status: str | None = None,
        gate_reason: str | None = None,
        phash: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        analyzed_at_ms: int | None = None,
        next_retry_at_ms: int | None = None,
        attempt_count: int | None = None,
        error_code: str | None = None,
        error_details: dict[str, Any] | None = None,
        summary_snippet: str | None = None,
        routing_status: str | None = None,
        routing_reason: str | None = None,
        routing_score: float | None = None,
        routing_metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._run_storage(
            self.storage.update_vision_frame_processing,
            session_id=session_id,
            frame_id=frame_id,
            processing_status=processing_status,
            gate_status=gate_status,
            gate_reason=gate_reason,
            phash=phash,
            provider=provider,
            model=model,
            analyzed_at_ms=analyzed_at_ms,
            next_retry_at_ms=next_retry_at_ms,
            attempt_count=attempt_count,
            error_code=error_code,
            error_details=error_details,
            summary_snippet=summary_snippet,
            routing_status=routing_status,
            routing_reason=routing_reason,
            routing_score=routing_score,
            routing_metadata=routing_metadata,
        )

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
            await self._update_frame_processing(
                session_id=frame_context.session_id,
                frame_id=dropped_frame_id,
                processing_status="superseded",
                gate_status="skipped",
                gate_reason="replaced_by_newer_inbox_frame",
                provider=self.provider_name,
                model=self.model_name,
            )
            logger.info(
                "VISION_INBOX_FRAME_REPLACED session=%s dropped_frame=%s new_frame=%s",
                frame_context.session_id,
                dropped_frame_id,
                frame_context.frame_id,
            )
            await self._cleanup_ingest_artifacts(
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
            budget_state = await self.provider_budget.get_state()
            if deferred.bootstrap_candidate and budget_state.available_now:
                try:
                    signal = self._build_signal_snapshot(
                        worker=worker,
                        pending_frame=deferred.pending_frame,
                        provider_budget_state=budget_state,
                    )
                    route = decide_vision_route(
                        signal=signal,
                        min_analysis_gap_seconds=self.settings.vision_min_analysis_gap_seconds,
                        scene_change_hamming_threshold=self.settings.vision_scene_change_hamming_threshold,
                        analysis_heartbeat_seconds=self.settings.vision_analysis_heartbeat_seconds,
                    )
                    if route.action == "analyze_now":
                        await self._analyze_now(
                            worker=worker,
                            pending_frame=deferred.pending_frame,
                            signal=signal,
                            route=route,
                        )
                    else:
                        await self._mark_store_only(
                            pending_frame=deferred.pending_frame,
                            signal=signal,
                            route=route,
                            provider_budget_state=budget_state,
                            reason="session_finalized_before_analysis",
                        )
                except VisionGateError:
                    await self._mark_store_only(
                        pending_frame=deferred.pending_frame,
                        signal=deferred.signal,
                        route=deferred.route,
                        provider_budget_state=budget_state,
                        reason="session_finalized_before_analysis",
                    )
            elif deferred.bootstrap_candidate:
                await self._persist_bootstrap_memory_state(
                    worker=worker,
                    status="bootstrap_degraded",
                    reason="session_finalized_during_provider_cooldown",
                    frame_id=deferred.pending_frame.frame_context.frame_id,
                    next_retry_at_ms=budget_state.available_at_ms,
                    attempt_count=await self._current_attempt_count(
                        session_id=deferred.pending_frame.frame_context.session_id,
                        frame_id=deferred.pending_frame.frame_context.frame_id,
                    ),
                    error_code="VISION_BOOTSTRAP_INCOMPLETE",
                )
                await self._update_frame_processing(
                    session_id=deferred.pending_frame.frame_context.session_id,
                    frame_id=deferred.pending_frame.frame_context.frame_id,
                    processing_status="bootstrap_degraded",
                    gate_status="accepted",
                    gate_reason="session_finalized_during_provider_cooldown",
                    phash=deferred.signal.dhash_hex,
                    provider=self.provider_name,
                    model=self.model_name,
                    next_retry_at_ms=budget_state.available_at_ms,
                    error_code="VISION_BOOTSTRAP_INCOMPLETE",
                    routing_status="bootstrap_degraded",
                    routing_reason="session_finalized_during_provider_cooldown",
                    routing_score=deferred.route.priority_score,
                    routing_metadata=self._build_routing_metadata(
                        signal=deferred.signal,
                        route=deferred.route,
                        provider_budget_state=budget_state,
                        analysis_outcome="bootstrap_degraded",
                    ),
                )
            else:
                await self._mark_store_only(
                    pending_frame=deferred.pending_frame,
                    signal=deferred.signal,
                    route=deferred.route,
                    provider_budget_state=build_budget_state_from_signal(deferred.signal),
                    reason="session_finalized_before_analysis",
                )
            worker.best_deferred_candidate = None
        if worker.accepted_event_count == 0 and worker.bootstrap_state != "bootstrap_degraded":
            await self._persist_bootstrap_memory_state(
                worker=worker,
                status="bootstrap_degraded",
                reason="session_ended_without_accepted_visual_observation",
                error_code="VISION_BOOTSTRAP_INCOMPLETE",
            )
        if worker.pending_session_events:
            await self._materialize_session_memory(worker)

    async def _ensure_worker(self, *, session_id: str) -> SessionVisionWorker:
        while True:
            owns_bootstrap = False
            bootstrap_future: asyncio.Future[SessionVisionWorker]
            async with self._workers_lock:
                worker = self._workers.get(session_id)
                if worker is not None:
                    if worker.task is not None and not worker.task.done():
                        return worker
                    self._workers.pop(session_id, None)
                    logger.error(
                        "VISION_WORKER_RESTARTING session=%s previous_error=%s",
                        session_id,
                        worker.last_worker_error or "task_ended",
                    )

                bootstrap_future = self._worker_bootstraps.get(session_id)
                if bootstrap_future is None:
                    bootstrap_future = asyncio.get_running_loop().create_future()
                    self._worker_bootstraps[session_id] = bootstrap_future
                    owns_bootstrap = True

            if not owns_bootstrap:
                return await bootstrap_future

            try:
                worker = await self._build_worker(session_id=session_id)
            except Exception as exc:
                async with self._workers_lock:
                    current = self._worker_bootstraps.pop(session_id, None)
                    if current is bootstrap_future and not bootstrap_future.done():
                        bootstrap_future.set_exception(exc)
                raise

            async with self._workers_lock:
                self._workers[session_id] = worker
                current = self._worker_bootstraps.pop(session_id, None)
                if current is bootstrap_future and not bootstrap_future.done():
                    bootstrap_future.set_result(worker)
            return worker

    async def _build_worker(self, *, session_id: str) -> SessionVisionWorker:
        session_storage = await self._run_storage(
            self.storage.ensure_session_storage,
            session_id=session_id,
        )
        accepted_events = await self._run_storage(
            self.storage.read_vision_events,
            session_id=session_id,
        )
        previous_session_memory = await self._run_storage(
            self.storage.read_session_memory,
            session_id=session_id,
        )
        previous_short_term_memory = await self._run_storage(
            self.storage.read_short_term_memory,
            session_id=session_id,
        )
        session_updated_at_ms = coerce_optional_int(previous_session_memory.get("updated_at_ms"))
        accepted_event_count = len(accepted_events)
        short_term_updated_at_ms = (
            coerce_positive_optional_int(previous_short_term_memory.get("window_end_ts_ms"))
            if accepted_event_count > 0
            else None
        )
        worker = SessionVisionWorker(
            session_id=session_id,
            session_storage=session_storage,
            last_successful_analysis_at_ms=short_term_updated_at_ms,
            short_term_memory_last_updated_at_ms=short_term_updated_at_ms,
            session_memory_last_updated_at_ms=short_term_updated_at_ms,
            session_memory_exists=accepted_event_count > 0,
            accepted_event_count=accepted_event_count,
            bootstrap_state="bootstrapped" if accepted_event_count > 0 else "unbootstrapped",
            last_session_rollup_at_ms=session_updated_at_ms if accepted_event_count > 0 else None,
        )
        for event in accepted_events:
            worker.short_term_window_events.append(event)
        self._prune_short_term_window_events(worker)
        worker.task = asyncio.create_task(
            self._run_session_worker(worker),
            name=f"vision-session-{session_id}",
        )
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
        except Exception as exc:
            worker.last_worker_error = f"{type(exc).__name__}: {exc}"
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
                    if deferred.bootstrap_candidate:
                        wake_at_ms = budget_state.available_at_ms
                    else:
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
            await self._handle_gate_failure(
                worker=worker,
                pending_frame=pending_frame,
                provider_budget_state=budget_state,
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
            await self._mark_drop_redundant(
                pending_frame=pending_frame,
                signal=signal,
                route=route,
                provider_budget_state=budget_state,
                reason=route.reason,
            )
            return
        if route.action == "store_only":
            await self._mark_store_only(
                pending_frame=pending_frame,
                signal=signal,
                route=route,
                provider_budget_state=budget_state,
                reason=route.reason,
            )
            return
        if route.action == "defer_candidate":
            await self._defer_candidate(
                worker=worker,
                pending_frame=pending_frame,
                signal=signal,
                route=route,
                provider_budget_state=budget_state,
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
        budget_state = await self.provider_budget.get_state()
        now_ts_ms = now_ms()
        deferred_ttl_ms = self.settings.vision_deferred_candidate_ttl_seconds * 1000
        expires_at_ms = deferred.deferred_at_ms + deferred_ttl_ms
        if not deferred.bootstrap_candidate and now_ts_ms >= expires_at_ms:
            await self._mark_store_only(
                pending_frame=deferred.pending_frame,
                signal=deferred.signal,
                route=deferred.route,
                provider_budget_state=budget_state,
                reason="deferred_candidate_expired",
            )
            worker.best_deferred_candidate = None
            return

        try:
            signal = self._build_signal_snapshot(
                worker=worker,
                pending_frame=deferred.pending_frame,
                provider_budget_state=budget_state,
            )
        except VisionGateError:
            await self._handle_gate_failure(
                worker=worker,
                pending_frame=deferred.pending_frame,
                provider_budget_state=budget_state,
            )
            worker.best_deferred_candidate = None
            return

        route = decide_vision_route(
            signal=signal,
            min_analysis_gap_seconds=self.settings.vision_min_analysis_gap_seconds,
            scene_change_hamming_threshold=self.settings.vision_scene_change_hamming_threshold,
            analysis_heartbeat_seconds=self.settings.vision_analysis_heartbeat_seconds,
        )
        if route.action == "defer_candidate":
            processing_status = "retry_pending" if deferred.bootstrap_candidate else "deferred"
            await self._update_frame_processing(
                session_id=deferred.pending_frame.frame_context.session_id,
                frame_id=deferred.pending_frame.frame_context.frame_id,
                processing_status=processing_status,
                gate_status="accepted",
                gate_reason=route.reason,
                phash=signal.dhash_hex,
                provider=self.provider_name,
                model=self.model_name,
                next_retry_at_ms=budget_state.available_at_ms,
                routing_status=route.action,
                routing_reason=route.reason,
                routing_score=route.priority_score,
                routing_metadata=self._build_routing_metadata(
                    signal=signal,
                    route=route,
                    provider_budget_state=budget_state,
                    analysis_outcome="deferred_candidate_retained",
                ),
            )
            if deferred.bootstrap_candidate:
                await self._persist_bootstrap_memory_state(
                    worker=worker,
                    status="bootstrap_pending",
                    reason="provider_budget_unavailable",
                    frame_id=deferred.pending_frame.frame_context.frame_id,
                    next_retry_at_ms=budget_state.available_at_ms,
                    attempt_count=await self._current_attempt_count(
                        session_id=deferred.pending_frame.frame_context.session_id,
                        frame_id=deferred.pending_frame.frame_context.frame_id,
                    ),
                )
            await self._append_routing_event(
                signal=signal,
                route=route,
                provider_budget_state=budget_state,
                did_attempt_analysis=False,
                analysis_outcome="deferred_candidate_retained",
            )
            return
        worker.best_deferred_candidate = None
        if route.action == "drop_redundant":
            await self._mark_drop_redundant(
                pending_frame=deferred.pending_frame,
                signal=signal,
                route=route,
                provider_budget_state=budget_state,
                reason=route.reason,
            )
            return
        if route.action == "store_only":
            await self._mark_store_only(
                pending_frame=deferred.pending_frame,
                signal=signal,
                route=route,
                provider_budget_state=budget_state,
                reason=route.reason,
            )
            return
        await self._analyze_now(
            worker=worker,
            pending_frame=deferred.pending_frame,
            signal=signal,
            route=route,
        )

    async def _handle_gate_failure(
        self,
        *,
        worker: SessionVisionWorker,
        pending_frame: PendingVisionFrame,
        provider_budget_state: VisionProviderBudgetState,
    ) -> None:
        fallback_signal = VisionSignalSnapshot(
            session_id=pending_frame.frame_context.session_id,
            frame_id=pending_frame.frame_context.frame_id,
            capture_ts_ms=pending_frame.frame_context.capture_ts_ms,
            is_first_frame=worker.last_accepted_frame is None,
            capture_gap_ms=None,
            dhash_hex="",
            hamming_distance=None,
            has_short_term_memory=worker.short_term_memory_last_updated_at_ms is not None,
            has_session_memory=worker.session_memory_last_updated_at_ms is not None,
            short_term_memory_age_ms=None,
            session_memory_age_ms=None,
            last_successful_analysis_at_ms=worker.last_successful_analysis_at_ms,
            last_analysis_failed=worker.last_analysis_failed,
            provider_available_now=provider_budget_state.available_now,
            provider_cooldown_until_ms=provider_budget_state.cooldown_until_ms,
            provider_budget_reason=provider_budget_state.reason,
        )
        await self._update_frame_processing(
            session_id=pending_frame.frame_context.session_id,
            frame_id=pending_frame.frame_context.frame_id,
            processing_status="gate_failed",
            gate_status="error",
            gate_reason="image_decode_failed",
            provider=self.provider_name,
            model=self.model_name,
            analyzed_at_ms=now_ms(),
            error_code="VISION_GATE_FAILED",
            error_details={"reason": "image_decode_failed"},
            routing_status="store_only",
            routing_reason="image_decode_failed",
            routing_score=0.0,
            routing_metadata={
                "analysis_outcome": "gate_failed",
                "provider_available_now": provider_budget_state.available_now,
                "provider_available_at_ms": provider_budget_state.available_at_ms,
                "provider_cooldown_until_ms": provider_budget_state.cooldown_until_ms,
                "provider_budget_reason": provider_budget_state.reason,
            },
        )
        await self._append_routing_event(
            signal=fallback_signal,
            route=None,
            provider_budget_state=provider_budget_state,
            did_attempt_analysis=False,
            analysis_outcome="gate_failed",
            fallback_action="store_only",
            fallback_reason="image_decode_failed",
        )
        worker.last_analysis_failed = True
        logger.exception(
            "VISION_SIGNAL_EXTRACTION_FAILED session=%s frame=%s",
            pending_frame.frame_context.session_id,
            pending_frame.frame_context.frame_id,
        )
        await self._cleanup_ingest_artifacts(
            session_id=pending_frame.frame_context.session_id,
            frame_id=pending_frame.frame_context.frame_id,
        )
