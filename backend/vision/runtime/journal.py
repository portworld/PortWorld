from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.vision.policy.gating import VisionProviderBudgetState, VisionRouteDecision, VisionSignalSnapshot
    from backend.vision.runtime.models import PendingVisionFrame


class VisionFrameJournalMixin:
    def _build_routing_metadata(
        self,
        *,
        signal: "VisionSignalSnapshot",
        route: "VisionRouteDecision | None",
        provider_budget_state: "VisionProviderBudgetState",
        analysis_outcome: str,
        retry_after_seconds: float | None = None,
        error_details: dict[str, object] | None = None,
    ) -> dict[str, object]:
        metadata: dict[str, object] = {
            "capture_gap_ms": signal.capture_gap_ms,
            "dhash_hex": signal.dhash_hex,
            "hamming_distance": signal.hamming_distance,
            "novelty_score": route.novelty_score if route is not None else None,
            "freshness_score": route.freshness_score if route is not None else None,
            "memory_bootstrap_required": route.memory_bootstrap_required if route is not None else None,
            "provider_available_now": provider_budget_state.available_now,
            "provider_available_at_ms": provider_budget_state.available_at_ms,
            "provider_cooldown_until_ms": provider_budget_state.cooldown_until_ms,
            "provider_budget_reason": provider_budget_state.reason,
            "provider_rate_limit_streak": provider_budget_state.consecutive_rate_limit_count,
            "analysis_outcome": analysis_outcome,
        }
        if retry_after_seconds is not None:
            metadata["retry_after_seconds"] = retry_after_seconds
        if error_details is not None:
            metadata["error_details"] = error_details
        return metadata

    def _build_route_decision_payload(
        self,
        *,
        signal: "VisionSignalSnapshot",
        route: "VisionRouteDecision | None",
        fallback_action: str,
        fallback_reason: str,
    ) -> dict[str, object]:
        if route is None:
            return {
                "session_id": signal.session_id,
                "frame_id": signal.frame_id,
                "action": fallback_action,
                "reason": fallback_reason,
                "priority_score": None,
                "novelty_score": None,
                "freshness_score": None,
                "memory_bootstrap_required": None,
                "provider_budget_available": signal.provider_available_now,
                "provider_cooldown_until_ms": signal.provider_cooldown_until_ms,
            }
        return {
            "session_id": route.session_id,
            "frame_id": route.frame_id,
            "action": route.action,
            "reason": route.reason,
            "priority_score": route.priority_score,
            "novelty_score": route.novelty_score,
            "freshness_score": route.freshness_score,
            "memory_bootstrap_required": route.memory_bootstrap_required,
            "provider_budget_available": route.provider_budget_available,
            "provider_cooldown_until_ms": route.provider_cooldown_until_ms,
        }

    async def _append_routing_event(
        self,
        *,
        signal: "VisionSignalSnapshot",
        route: "VisionRouteDecision | None",
        provider_budget_state: "VisionProviderBudgetState",
        did_attempt_analysis: bool,
        analysis_outcome: str,
        retry_after_seconds: float | None = None,
        error_details: dict[str, object] | None = None,
        fallback_action: str = "store_only",
        fallback_reason: str = "route_unavailable",
    ) -> None:
        event = {
            "frame_id": signal.frame_id,
            "capture_ts_ms": signal.capture_ts_ms,
            "signal_snapshot": {
                "session_id": signal.session_id,
                "frame_id": signal.frame_id,
                "capture_ts_ms": signal.capture_ts_ms,
                "is_first_frame": signal.is_first_frame,
                "capture_gap_ms": signal.capture_gap_ms,
                "dhash_hex": signal.dhash_hex,
                "hamming_distance": signal.hamming_distance,
                "has_short_term_memory": signal.has_short_term_memory,
                "has_session_memory": signal.has_session_memory,
                "short_term_memory_age_ms": signal.short_term_memory_age_ms,
                "session_memory_age_ms": signal.session_memory_age_ms,
                "last_successful_analysis_at_ms": signal.last_successful_analysis_at_ms,
                "last_analysis_failed": signal.last_analysis_failed,
                "provider_available_now": signal.provider_available_now,
                "provider_cooldown_until_ms": signal.provider_cooldown_until_ms,
                "provider_budget_reason": signal.provider_budget_reason,
            },
            "route_decision": self._build_route_decision_payload(
                signal=signal,
                route=route,
                fallback_action=fallback_action,
                fallback_reason=fallback_reason,
            ),
            "provider_budget_state": {
                "available_now": provider_budget_state.available_now,
                "available_at_ms": provider_budget_state.available_at_ms,
                "cooldown_until_ms": provider_budget_state.cooldown_until_ms,
                "consecutive_rate_limit_count": provider_budget_state.consecutive_rate_limit_count,
                "reason": provider_budget_state.reason,
            },
            "did_attempt_analysis": did_attempt_analysis,
            "analysis_outcome": analysis_outcome,
        }
        if retry_after_seconds is not None:
            event["retry_after_seconds"] = retry_after_seconds
        if error_details is not None:
            event["error_details"] = error_details
        await self._run_storage(
            self.storage.append_vision_routing_event,
            session_id=signal.session_id,
            event=event,
        )

    async def _mark_drop_redundant(
        self,
        *,
        pending_frame: "PendingVisionFrame",
        signal: "VisionSignalSnapshot",
        route: "VisionRouteDecision | None",
        provider_budget_state: "VisionProviderBudgetState",
        reason: str,
    ) -> None:
        await self._run_storage(
            self.storage.update_vision_frame_processing,
            session_id=pending_frame.frame_context.session_id,
            frame_id=pending_frame.frame_context.frame_id,
            processing_status="gated_rejected",
            gate_status="rejected",
            gate_reason=reason,
            phash=signal.dhash_hex,
            provider=self.provider_name,
            model=self.model_name,
            routing_status="drop_redundant",
            routing_reason=reason,
            routing_score=route.priority_score if route is not None else None,
            routing_metadata=self._build_routing_metadata(
                signal=signal,
                route=route,
                provider_budget_state=provider_budget_state,
                analysis_outcome="dropped_redundant",
            ),
        )
        await self._append_routing_event(
            signal=signal,
            route=route,
            provider_budget_state=provider_budget_state,
            did_attempt_analysis=False,
            analysis_outcome="dropped_redundant",
            fallback_action="drop_redundant",
            fallback_reason=reason,
        )
        await self._cleanup_ingest_artifacts(
            session_id=pending_frame.frame_context.session_id,
            frame_id=pending_frame.frame_context.frame_id,
        )

    async def _mark_store_only(
        self,
        *,
        pending_frame: "PendingVisionFrame",
        signal: "VisionSignalSnapshot",
        route: "VisionRouteDecision | None",
        provider_budget_state: "VisionProviderBudgetState",
        reason: str,
    ) -> None:
        await self._run_storage(
            self.storage.update_vision_frame_processing,
            session_id=pending_frame.frame_context.session_id,
            frame_id=pending_frame.frame_context.frame_id,
            processing_status="stored_only",
            gate_status="accepted",
            gate_reason=reason,
            phash=signal.dhash_hex,
            provider=self.provider_name,
            model=self.model_name,
            routing_status="store_only",
            routing_reason=reason,
            routing_score=route.priority_score if route is not None else None,
            routing_metadata=self._build_routing_metadata(
                signal=signal,
                route=route,
                provider_budget_state=provider_budget_state,
                analysis_outcome="stored_only",
            ),
        )
        await self._append_routing_event(
            signal=signal,
            route=route,
            provider_budget_state=provider_budget_state,
            did_attempt_analysis=False,
            analysis_outcome="stored_only",
            fallback_action="store_only",
            fallback_reason=reason,
        )
        await self._cleanup_ingest_artifacts(
            session_id=pending_frame.frame_context.session_id,
            frame_id=pending_frame.frame_context.frame_id,
        )
