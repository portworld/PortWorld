from __future__ import annotations

import inspect

from backend.core.storage import now_ms
from backend.memory.materializer import (
    build_session_memory_rollup,
    build_short_term_memory,
    coerce_short_term_memory_payload,
)
from backend.vision.runtime.models import coerce_optional_int, latest_capture_ts_from_events


class VisionMemoryProjectionMixin:
    def _initialize_storage_writer_capabilities(self) -> None:
        short_term_parameters = inspect.signature(self.storage.write_short_term_memory).parameters
        session_parameters = inspect.signature(self.storage.write_session_memory).parameters
        self._storage_supports_short_term_payload = "payload" in short_term_parameters
        self._storage_supports_session_payload = "payload" in session_parameters

    def _prune_short_term_window_events(self, worker) -> None:
        if not worker.short_term_window_events:
            return
        latest_capture_ts_ms = latest_capture_ts_from_events(list(worker.short_term_window_events))
        if latest_capture_ts_ms is None:
            worker.short_term_window_events.clear()
            return
        window_start_ts_ms = max(
            0,
            latest_capture_ts_ms - (self.settings.vision_short_term_window_seconds * 1000),
        )
        while worker.short_term_window_events:
            oldest_capture_ts_ms = coerce_optional_int(
                worker.short_term_window_events[0].get("capture_ts_ms")
            )
            if oldest_capture_ts_ms is None:
                worker.short_term_window_events.popleft()
                continue
            if oldest_capture_ts_ms >= window_start_ts_ms:
                break
            worker.short_term_window_events.popleft()

    def _append_short_term_window_event(self, *, worker, event: dict[str, object]) -> None:
        worker.short_term_window_events.append(event)
        self._prune_short_term_window_events(worker)

    async def _materialize_short_term_memory(self, worker) -> None:
        self._prune_short_term_window_events(worker)
        accepted_events = list(worker.short_term_window_events)
        payload, markdown_text = build_short_term_memory(
            session_id=worker.session_id,
            accepted_events=accepted_events,
            window_seconds=self.settings.vision_short_term_window_seconds,
        )
        await self._run_storage(
            self._write_short_term_memory,
            session_id=worker.session_id,
            payload=payload,
            markdown_text=markdown_text,
        )
        worker.short_term_memory_last_updated_at_ms = coerce_optional_int(payload.get("window_end_ts_ms"))

    def _should_roll_session_memory(self, worker) -> bool:
        if not worker.pending_session_events:
            return False
        if worker.last_session_rollup_at_ms is None:
            return True
        if len(worker.pending_session_events) >= self.settings.vision_session_rollup_min_accepted_events:
            return True
        elapsed_ms = now_ms() - worker.last_session_rollup_at_ms
        return elapsed_ms >= self.settings.vision_session_rollup_interval_seconds * 1000

    async def _materialize_session_memory(self, worker) -> None:
        previous_memory = await self._run_storage(
            self.storage.read_session_memory,
            session_id=worker.session_id,
        )
        payload, markdown_text = build_session_memory_rollup(
            session_id=worker.session_id,
            previous_memory=previous_memory,
            recent_events=list(worker.pending_session_events),
        )
        await self._run_storage(
            self._write_session_memory,
            session_id=worker.session_id,
            payload=payload,
            markdown_text=markdown_text,
        )
        latest_capture_ts_ms = latest_capture_ts_from_events(worker.pending_session_events)
        worker.pending_session_events.clear()
        worker.last_session_rollup_at_ms = int(payload["updated_at_ms"])
        if latest_capture_ts_ms is not None:
            worker.session_memory_last_updated_at_ms = latest_capture_ts_ms
        worker.session_memory_exists = True
        worker.bootstrap_state = "bootstrapped"

    async def _cleanup_ingest_artifacts(self, *, session_id: str, frame_id: str) -> None:
        if self.settings.vision_debug_retain_raw_frames:
            return
        await self._run_storage(
            self.storage.delete_vision_ingest_artifacts,
            session_id=session_id,
            frame_id=frame_id,
        )

    async def _current_attempt_count(self, *, session_id: str, frame_id: str) -> int:
        record = await self._run_storage(
            self.storage.get_vision_frame_record,
            session_id=session_id,
            frame_id=frame_id,
        )
        if record is None:
            return 0
        return record.attempt_count

    async def _persist_bootstrap_memory_state(
        self,
        *,
        worker,
        status: str,
        reason: str,
        frame_id: str | None = None,
        next_retry_at_ms: int | None = None,
        attempt_count: int | None = None,
        error_code: str | None = None,
        error_details: dict[str, object] | None = None,
        last_attempt_at_ms: int | None = None,
    ) -> None:
        updated_at_ms = now_ms()
        short_term_payload = {
            "session_id": worker.session_id,
            "status": status,
            "updated_at_ms": updated_at_ms,
            "reason": reason,
            "provider": self.provider_name,
            "model": self.model_name,
            "bootstrap_frame_id": frame_id,
            "next_retry_at_ms": next_retry_at_ms,
            "last_attempt_at_ms": last_attempt_at_ms,
            "attempt_count": attempt_count or 0,
            "error_code": error_code,
            "error_details": error_details or {},
            "current_scene_summary": "",
            "recent_entities": [],
            "recent_actions": [],
            "recent_visible_text": [],
            "recent_documents": [],
            "source_frame_ids": [frame_id] if frame_id else [],
        }
        short_term_markdown = "\n".join(
            [
                "# Short-Term Memory",
                "",
                "## Current View",
                "Visual memory bootstrap has not completed yet.",
                "",
                "## Recent Changes",
                f"- Status: {status}",
                f"- Reason: {reason}",
                f"- Provider: {self.provider_name}",
                f"- Model: {self.model_name}",
                f"- Bootstrap frame: {frame_id or 'none'}",
                f"- Next retry: {next_retry_at_ms if next_retry_at_ms is not None else 'none'}",
                f"- Last attempt: {last_attempt_at_ms if last_attempt_at_ms is not None else 'none'}",
                f"- Attempt count: {attempt_count or 0}",
                f"- Error code: {error_code or 'none'}",
                "",
                "## Current Task Guess",
                "Unknown",
                "",
                "## Timestamp",
                str(updated_at_ms),
                "",
            ]
        )
        session_payload = {
            "session_id": worker.session_id,
            "status": status,
            "updated_at_ms": updated_at_ms,
            "started_at_ms": worker.last_session_rollup_at_ms or updated_at_ms,
            "reason": reason,
            "provider": self.provider_name,
            "model": self.model_name,
            "bootstrap_frame_id": frame_id,
            "next_retry_at_ms": next_retry_at_ms,
            "last_attempt_at_ms": last_attempt_at_ms,
            "attempt_count": attempt_count or 0,
            "error_code": error_code,
            "error_details": error_details or {},
            "accepted_event_count": worker.accepted_event_count,
            "summary_text": "Visual memory bootstrap has not completed yet.",
        }
        session_markdown = "\n".join(
            [
                "# Session Memory",
                "",
                "## Session Goal",
                "Unknown",
                "",
                "## What Happened",
                "Visual memory bootstrap has not completed yet.",
                "",
                "## Important Facts Learned",
                f"- Status: {status}",
                f"- Reason: {reason}",
                f"- Provider: {self.provider_name}",
                f"- Model: {self.model_name}",
                f"- Bootstrap frame: {frame_id or 'none'}",
                f"- Next retry: {next_retry_at_ms if next_retry_at_ms is not None else 'none'}",
                f"- Last attempt: {last_attempt_at_ms if last_attempt_at_ms is not None else 'none'}",
                f"- Attempt count: {attempt_count or 0}",
                f"- Error code: {error_code or 'none'}",
                "",
                "## Pending Follow-Ups",
                "Visual memory bootstrap has not completed yet.",
                "",
                "## Last Updated",
                str(updated_at_ms),
                "",
            ]
        )
        await self._run_storage(
            self._write_short_term_memory,
            session_id=worker.session_id,
            payload=short_term_payload,
            markdown_text=short_term_markdown,
        )
        await self._run_storage(
            self._write_session_memory,
            session_id=worker.session_id,
            payload=session_payload,
            markdown_text=session_markdown,
        )
        short_term_state = coerce_short_term_memory_payload(short_term_payload)
        worker.short_term_memory_last_updated_at_ms = coerce_optional_int(
            short_term_state.get("window_end_ts_ms")
        )
        worker.session_memory_last_updated_at_ms = None
        worker.session_memory_exists = False
        worker.bootstrap_state = status

    def _write_short_term_memory(
        self,
        *,
        session_id: str,
        payload: dict[str, object],
        markdown_text: str,
    ) -> None:
        writer = self.storage.write_short_term_memory
        if self._storage_supports_short_term_payload is None:
            self._initialize_storage_writer_capabilities()
        if self._storage_supports_short_term_payload:
            writer(session_id=session_id, payload=payload, markdown_text=markdown_text)
            return
        writer(session_id=session_id, markdown=markdown_text)

    def _write_session_memory(
        self,
        *,
        session_id: str,
        payload: dict[str, object],
        markdown_text: str,
    ) -> None:
        writer = self.storage.write_session_memory
        if self._storage_supports_session_payload is None:
            self._initialize_storage_writer_capabilities()
        if self._storage_supports_session_payload:
            writer(session_id=session_id, payload=payload, markdown_text=markdown_text)
            return
        writer(session_id=session_id, markdown=markdown_text)
