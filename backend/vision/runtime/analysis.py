from __future__ import annotations

import logging
from typing import Literal

from backend.core.storage import now_ms
from backend.memory.materializer import build_accepted_vision_event
from backend.vision.contracts import VisionProviderError, VisionRateLimitError
from backend.vision.policy.gating import AcceptedFrameReference, VisionRouteDecision, extract_vision_signal_snapshot
from backend.vision.runtime.models import (
    DeferredVisionCandidate,
    PendingVisionFrame,
    compute_age_ms,
    is_candidate_stronger,
)

logger = logging.getLogger(__name__)


class VisionAnalysisMixin:
    def _build_signal_snapshot(
        self,
        *,
        worker,
        pending_frame: PendingVisionFrame,
        provider_budget_state,
    ):
        short_term_age_ms = compute_age_ms(
            current_capture_ts_ms=pending_frame.frame_context.capture_ts_ms,
            memory_ts_ms=worker.short_term_memory_last_updated_at_ms,
        )
        session_age_ms = compute_age_ms(
            current_capture_ts_ms=pending_frame.frame_context.capture_ts_ms,
            memory_ts_ms=worker.session_memory_last_updated_at_ms,
        )
        return extract_vision_signal_snapshot(
            image_bytes=pending_frame.image_bytes,
            frame_context=pending_frame.frame_context,
            last_accepted_frame=worker.last_accepted_frame,
            has_short_term_memory=worker.short_term_memory_last_updated_at_ms is not None,
            has_session_memory=worker.session_memory_exists,
            short_term_memory_age_ms=short_term_age_ms,
            session_memory_age_ms=session_age_ms,
            last_successful_analysis_at_ms=worker.last_successful_analysis_at_ms,
            last_analysis_failed=worker.last_analysis_failed,
            provider_budget_state=provider_budget_state,
        )

    async def _defer_candidate(
        self,
        *,
        worker: "SessionVisionWorker",
        pending_frame: PendingVisionFrame,
        signal: "VisionSignalSnapshot",
        route: VisionRouteDecision,
        provider_budget_state: "VisionProviderBudgetState",
    ) -> None:
        bootstrap_candidate = route.memory_bootstrap_required and worker.accepted_event_count == 0
        incoming = DeferredVisionCandidate(
            pending_frame=pending_frame,
            signal=signal,
            route=route,
            deferred_at_ms=now_ms(),
            bootstrap_candidate=bootstrap_candidate,
        )
        existing = worker.best_deferred_candidate
        selection = self._select_deferred_candidate_action(existing=existing, incoming=incoming)
        if selection == "keep_existing_bootstrap":
            await self._mark_store_only(
                pending_frame=pending_frame,
                signal=signal,
                route=route,
                provider_budget_state=provider_budget_state,
                reason="bootstrap_candidate_already_pending",
            )
            return
        if selection in {"select", "replace_bootstrap", "replace"}:
            replace_reason: str | None = None
            if selection == "replace_bootstrap":
                replace_reason = "bootstrap_candidate_replaced_by_stronger_frame"
            elif selection == "replace":
                replace_reason = "deferred_replaced_by_higher_priority_candidate"
            await self._persist_selected_deferred_candidate(
                worker=worker,
                incoming=incoming,
                provider_budget_state=provider_budget_state,
                replaced_candidate=existing,
                replaced_reason=replace_reason,
            )
            return
        await self._mark_store_only(
            pending_frame=pending_frame,
            signal=signal,
            route=route,
            provider_budget_state=provider_budget_state,
            reason="deferred_not_selected_lower_priority",
        )

    def _select_deferred_candidate_action(
        self,
        *,
        existing: DeferredVisionCandidate | None,
        incoming: DeferredVisionCandidate,
    ) -> Literal["select", "replace_bootstrap", "replace", "keep_existing_bootstrap", "keep_existing"]:
        if existing is None:
            return "select"
        if existing.bootstrap_candidate and incoming.bootstrap_candidate:
            if incoming.route.priority_score > existing.route.priority_score:
                return "replace_bootstrap"
            return "keep_existing_bootstrap"
        if is_candidate_stronger(incoming, existing):
            return "replace"
        return "keep_existing"

    async def _persist_selected_deferred_candidate(
        self,
        *,
        worker: "SessionVisionWorker",
        incoming: DeferredVisionCandidate,
        provider_budget_state: "VisionProviderBudgetState",
        replaced_candidate: DeferredVisionCandidate | None,
        replaced_reason: str | None,
    ) -> None:
        signal = incoming.signal
        route = incoming.route
        worker.best_deferred_candidate = incoming
        await self._update_frame_processing(
            session_id=signal.session_id,
            frame_id=signal.frame_id,
            processing_status="retry_pending" if incoming.bootstrap_candidate else "deferred",
            gate_status="accepted",
            gate_reason=route.reason,
            phash=signal.dhash_hex,
            next_retry_at_ms=provider_budget_state.available_at_ms,
            routing_status=route.action,
            routing_reason=route.reason,
            routing_score=route.priority_score,
            routing_metadata=self._build_routing_metadata(
                signal=signal,
                route=route,
                provider_budget_state=provider_budget_state,
                analysis_outcome="deferred_candidate_selected",
            ),
        )
        if replaced_candidate is not None and replaced_reason is not None:
            await self._mark_store_only(
                pending_frame=replaced_candidate.pending_frame,
                signal=replaced_candidate.signal,
                route=replaced_candidate.route,
                provider_budget_state=provider_budget_state,
                reason=replaced_reason,
            )
        if incoming.bootstrap_candidate:
            worker.bootstrap_state = "bootstrap_pending"
            await self._persist_bootstrap_memory_state(
                worker=worker,
                status="bootstrap_pending",
                reason=route.reason,
                frame_id=signal.frame_id,
                next_retry_at_ms=provider_budget_state.available_at_ms,
                attempt_count=await self._current_attempt_count(
                    session_id=signal.session_id,
                    frame_id=signal.frame_id,
                ),
            )
        await self._append_routing_event(
            signal=signal,
            route=route,
            provider_budget_state=provider_budget_state,
            did_attempt_analysis=False,
            analysis_outcome="deferred_candidate_selected",
        )
        async with worker.condition:
            worker.condition.notify_all()

    async def _handle_terminal_analysis_failure(
        self,
        *,
        worker: "SessionVisionWorker",
        pending_frame: PendingVisionFrame,
        signal: "VisionSignalSnapshot",
        route: VisionRouteDecision,
        slot_state: "VisionProviderBudgetState",
        error_code: str,
        error_details: dict[str, object],
        bootstrap_reason: str,
    ) -> None:
        session_id = pending_frame.frame_context.session_id
        frame_id = pending_frame.frame_context.frame_id
        await self._update_frame_processing(
            session_id=session_id,
            frame_id=frame_id,
            processing_status="analysis_failed",
            gate_status="accepted",
            gate_reason=route.reason,
            phash=signal.dhash_hex,
            analyzed_at_ms=now_ms(),
            next_retry_at_ms=None,
            attempt_count=await self._current_attempt_count(
                session_id=session_id,
                frame_id=frame_id,
            ),
            error_code=error_code,
            error_details=error_details,
            routing_status=route.action,
            routing_reason=route.reason,
            routing_score=route.priority_score,
            routing_metadata=self._build_routing_metadata(
                signal=signal,
                route=route,
                provider_budget_state=slot_state,
                analysis_outcome="analysis_failed",
                error_details=error_details,
            ),
        )
        await self._append_routing_event(
            signal=signal,
            route=route,
            provider_budget_state=slot_state,
            did_attempt_analysis=True,
            analysis_outcome="analysis_failed",
            error_details=error_details,
        )
        worker.last_analysis_failed = True
        worker.best_deferred_candidate = None
        if route.memory_bootstrap_required and worker.accepted_event_count == 0:
            worker.bootstrap_state = "bootstrap_degraded"
            await self._persist_bootstrap_memory_state(
                worker=worker,
                status="bootstrap_degraded",
                reason=bootstrap_reason,
                frame_id=frame_id,
                attempt_count=await self._current_attempt_count(
                    session_id=session_id,
                    frame_id=frame_id,
                ),
                error_code=error_code,
                error_details=error_details,
                last_attempt_at_ms=now_ms(),
            )
        provider_message = str(error_details.get("provider_message") or "").strip()
        payload_excerpt = str(error_details.get("payload_excerpt") or "").strip()
        logger.warning(
            "VISION_ANALYSIS_FAILED session=%s frame=%s provider=%s model=%s status=%s provider_error_code=%s provider_message=%s payload_excerpt=%s session_closing=%s runtime_shutting_down=%s",
            session_id,
            frame_id,
            self.provider_name,
            self.model_name,
            error_details.get("http_status"),
            error_details.get("provider_error_code"),
            provider_message[:220] if provider_message else None,
            payload_excerpt[:220] if payload_excerpt else None,
            worker.close_requested,
            self._shutdown_requested,
        )
        await self._cleanup_ingest_artifacts(
            session_id=session_id,
            frame_id=frame_id,
        )

    async def _handle_rate_limit_failure(
        self,
        *,
        worker: "SessionVisionWorker",
        pending_frame: PendingVisionFrame,
        signal: "VisionSignalSnapshot",
        route: VisionRouteDecision,
        exc: VisionRateLimitError,
    ) -> None:
        await self.provider_budget.record_rate_limit(exc.retry_after_seconds)
        cooldown_state = await self.provider_budget.get_state()
        error_details = {
            "http_status": exc.status_code,
            "provider_error_code": exc.provider_error_code,
            "provider_message": exc.provider_message,
            "payload_excerpt": exc.payload_excerpt,
        }
        session_id = pending_frame.frame_context.session_id
        frame_id = pending_frame.frame_context.frame_id
        bootstrap_retry = route.memory_bootstrap_required and worker.accepted_event_count == 0
        await self._update_frame_processing(
            session_id=session_id,
            frame_id=frame_id,
            processing_status="retry_pending" if bootstrap_retry else "analysis_rate_limited",
            gate_status="accepted",
            gate_reason=route.reason,
            phash=signal.dhash_hex,
            analyzed_at_ms=now_ms(),
            next_retry_at_ms=cooldown_state.available_at_ms,
            attempt_count=await self._current_attempt_count(
                session_id=session_id,
                frame_id=frame_id,
            ),
            error_code="VISION_ANALYSIS_RATE_LIMITED",
            error_details=error_details,
            routing_status="analysis_rate_limited",
            routing_reason="provider_rate_limited",
            routing_score=route.priority_score,
            routing_metadata=self._build_routing_metadata(
                signal=signal,
                route=route,
                provider_budget_state=cooldown_state,
                analysis_outcome="analysis_rate_limited",
                retry_after_seconds=exc.retry_after_seconds,
                error_details=error_details,
            ),
        )
        await self._append_routing_event(
            signal=signal,
            route=route,
            provider_budget_state=cooldown_state,
            did_attempt_analysis=True,
            analysis_outcome="analysis_rate_limited",
            retry_after_seconds=exc.retry_after_seconds,
            error_details=error_details,
        )
        worker.last_analysis_failed = True
        if bootstrap_retry:
            worker.best_deferred_candidate = DeferredVisionCandidate(
                pending_frame=pending_frame,
                signal=signal,
                route=route,
                deferred_at_ms=now_ms(),
                bootstrap_candidate=True,
            )
            worker.bootstrap_state = "bootstrap_pending"
            await self._persist_bootstrap_memory_state(
                worker=worker,
                status="bootstrap_pending",
                reason="provider_rate_limited",
                frame_id=frame_id,
                next_retry_at_ms=cooldown_state.available_at_ms,
                attempt_count=await self._current_attempt_count(
                    session_id=session_id,
                    frame_id=frame_id,
                ),
                error_code="VISION_ANALYSIS_RATE_LIMITED",
                error_details=error_details,
                last_attempt_at_ms=now_ms(),
            )
        logger.warning(
            "VISION_ANALYSIS_RATE_LIMITED session=%s frame=%s provider=%s model=%s cooldown_until_ms=%s retry_after_seconds=%s",
            session_id,
            frame_id,
            self.provider_name,
            self.model_name,
            cooldown_state.cooldown_until_ms,
            exc.retry_after_seconds,
        )
        if not bootstrap_retry:
            await self._cleanup_ingest_artifacts(
                session_id=session_id,
                frame_id=frame_id,
            )

    async def _analyze_now(
        self,
        *,
        worker: "SessionVisionWorker",
        pending_frame: PendingVisionFrame,
        signal: "VisionSignalSnapshot",
        route: VisionRouteDecision,
    ) -> None:
        slot_state = await self.provider_budget.acquire_analysis_slot()
        if not slot_state.available_now:
            deferred_route = VisionRouteDecision(
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
            )
            await self._defer_candidate(
                worker=worker,
                pending_frame=pending_frame,
                signal=signal,
                route=deferred_route,
                provider_budget_state=slot_state,
            )
            return

        await self._update_frame_processing(
            session_id=pending_frame.frame_context.session_id,
            frame_id=pending_frame.frame_context.frame_id,
            processing_status="analyzing",
            gate_status="accepted",
            gate_reason=route.reason,
            phash=signal.dhash_hex,
            next_retry_at_ms=None,
            attempt_count=(await self._current_attempt_count(
                session_id=pending_frame.frame_context.session_id,
                frame_id=pending_frame.frame_context.frame_id,
            )) + 1,
            routing_status=route.action,
            routing_reason=route.reason,
            routing_score=route.priority_score,
            routing_metadata=self._build_routing_metadata(
                signal=signal,
                route=route,
                provider_budget_state=slot_state,
                analysis_outcome="analyzing",
            ),
        )
        if route.memory_bootstrap_required and worker.accepted_event_count == 0:
            worker.bootstrap_state = "bootstrap_pending"
        try:
            observation = await self.analyzer.analyze_frame(
                image_bytes=pending_frame.image_bytes,
                frame_context=pending_frame.frame_context,
                image_media_type=pending_frame.image_media_type,
            )
        except VisionRateLimitError as exc:
            await self._handle_rate_limit_failure(
                worker=worker,
                pending_frame=pending_frame,
                signal=signal,
                route=route,
                exc=exc,
            )
            return
        except VisionProviderError as exc:
            await self.provider_budget.record_non_rate_limit_failure()
            await self._handle_terminal_analysis_failure(
                worker=worker,
                pending_frame=pending_frame,
                signal=signal,
                route=route,
                slot_state=slot_state,
                error_code="VISION_ANALYSIS_FAILED",
                error_details={
                    "http_status": exc.status_code,
                    "provider_error_code": exc.provider_error_code,
                    "provider_message": exc.provider_message,
                    "payload_excerpt": exc.payload_excerpt,
                },
                bootstrap_reason="provider_request_failed",
            )
            return
        except Exception:
            await self.provider_budget.record_non_rate_limit_failure()
            await self._handle_terminal_analysis_failure(
                worker=worker,
                pending_frame=pending_frame,
                signal=signal,
                route=route,
                slot_state=slot_state,
                error_code="VISION_ANALYSIS_FAILED",
                error_details={"error_type": "unexpected_analysis_failure"},
                bootstrap_reason="unexpected_analysis_failure",
            )
            return

        await self.provider_budget.record_success()
        worker.last_analysis_failed = False
        worker.bootstrap_state = "bootstrapped"
        worker.best_deferred_candidate = None
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
        await self._run_storage(
            self.storage.append_vision_event,
            session_id=observation.session_id,
            event=accepted_event,
        )
        worker.accepted_event_count += 1
        worker.pending_session_events.append(accepted_event)
        self._append_short_term_window_event(worker=worker, event=accepted_event)
        await self._materialize_short_term_memory(worker)
        if self._should_roll_session_memory(worker):
            await self._materialize_session_memory(worker)
        await self._update_frame_processing(
            session_id=observation.session_id,
            frame_id=observation.frame_id,
            processing_status="analyzed",
            gate_status="accepted",
            gate_reason=route.reason,
            phash=signal.dhash_hex,
            analyzed_at_ms=now_ms(),
            next_retry_at_ms=None,
            error_details=None,
            summary_snippet=observation.scene_summary[:240],
            routing_status=route.action,
            routing_reason=route.reason,
            routing_score=route.priority_score,
            routing_metadata=self._build_routing_metadata(
                signal=signal,
                route=route,
                provider_budget_state=slot_state,
                analysis_outcome="analyzed",
            ),
        )
        await self._append_routing_event(
            signal=signal,
            route=route,
            provider_budget_state=slot_state,
            did_attempt_analysis=True,
            analysis_outcome="analyzed",
        )
        logger.info(
            "VISION_ANALYSIS_ACCEPTED session=%s frame=%s provider=%s model=%s scene_summary=%s",
            observation.session_id,
            observation.frame_id,
            self.provider_name,
            self.model_name,
            observation.scene_summary,
        )
        await self._cleanup_ingest_artifacts(
            session_id=observation.session_id,
            frame_id=observation.frame_id,
        )
