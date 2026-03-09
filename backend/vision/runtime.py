from __future__ import annotations

import asyncio
import logging
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
from backend.vision.gating import AcceptedFrameReference, VisionGateError, evaluate_frame_gate

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PendingVisionFrame:
    image_bytes: bytes
    frame_context: VisionFrameContext
    image_media_type: str = "image/jpeg"


@dataclass(frozen=True, slots=True)
class GateRecord:
    frame_id: str
    capture_ts_ms: int
    accepted: bool
    reason: str
    dhash_hex: str
    hamming_distance: int | None


@dataclass(slots=True)
class SessionVisionWorker:
    session_id: str
    session_storage: SessionStorageResult
    pending_frame: PendingVisionFrame | None = None
    latest_gate_records: deque[GateRecord] = field(default_factory=lambda: deque(maxlen=25))
    last_accepted_frame: AcceptedFrameReference | None = None
    last_observation: VisionObservation | None = None
    pending_session_events: list[dict[str, object]] = field(default_factory=list)
    last_session_rollup_at_ms: int | None = None
    close_requested: bool = False
    task: asyncio.Task[None] | None = None
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)


@dataclass(slots=True)
class VisionMemoryRuntime:
    settings: Settings
    storage: BackendStorage
    analyzer: VisionAnalyzer
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
        pending_frame = PendingVisionFrame(
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
            dropped_frame_id = worker.pending_frame.frame_context.frame_id if worker.pending_frame else None
            worker.pending_frame = pending_frame
            worker.condition.notify_all()
        if dropped_frame_id is not None:
            self.storage.update_vision_frame_processing(
                session_id=frame_context.session_id,
                frame_id=dropped_frame_id,
                processing_status="superseded",
                gate_status="skipped",
                gate_reason="replaced_by_newer_pending_frame",
                provider=self.provider_name,
                model=self.model_name,
                error_code=None,
            )
            logger.info(
                "VISION_PENDING_FRAME_REPLACED session=%s dropped_frame=%s new_frame=%s",
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

        if worker.pending_session_events:
            self._materialize_session_memory(worker)

    async def _ensure_worker(self, *, session_id: str) -> SessionVisionWorker:
        async with self._workers_lock:
            worker = self._workers.get(session_id)
            if worker is None:
                session_storage = self.storage.ensure_session_storage(session_id=session_id)
                previous_session_memory = self.storage.read_session_memory(session_id=session_id)
                worker = SessionVisionWorker(
                    session_id=session_id,
                    session_storage=session_storage,
                    last_session_rollup_at_ms=_coerce_optional_int(
                        previous_session_memory.get("updated_at_ms")
                    ),
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
                pending_frame = await self._wait_for_pending_frame(worker)
                if pending_frame is None:
                    if self._shutdown_requested or worker.close_requested:
                        break
                    continue
                await self._process_pending_frame(worker, pending_frame)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("VISION_SESSION_WORKER_FAILED session=%s", worker.session_id)

    async def _wait_for_pending_frame(self, worker: SessionVisionWorker) -> PendingVisionFrame | None:
        async with worker.condition:
            while (
                worker.pending_frame is None
                and not self._shutdown_requested
                and not worker.close_requested
            ):
                await worker.condition.wait()
            pending_frame = worker.pending_frame
            worker.pending_frame = None
            return pending_frame

    async def _process_pending_frame(
        self,
        worker: SessionVisionWorker,
        pending_frame: PendingVisionFrame,
    ) -> None:
        try:
            gate_decision = evaluate_frame_gate(
                image_bytes=pending_frame.image_bytes,
                frame_context=pending_frame.frame_context,
                last_accepted_frame=worker.last_accepted_frame,
                min_analysis_gap_seconds=self.settings.vision_min_analysis_gap_seconds,
                scene_change_hamming_threshold=self.settings.vision_scene_change_hamming_threshold,
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
            logger.exception(
                "VISION_GATE_FAILED session=%s frame=%s",
                pending_frame.frame_context.session_id,
                pending_frame.frame_context.frame_id,
            )
            self._cleanup_ingest_artifacts(
                session_id=pending_frame.frame_context.session_id,
                frame_id=pending_frame.frame_context.frame_id,
            )
            return

        gate_record = GateRecord(
            frame_id=pending_frame.frame_context.frame_id,
            capture_ts_ms=pending_frame.frame_context.capture_ts_ms,
            accepted=gate_decision.accepted,
            reason=gate_decision.reason,
            dhash_hex=gate_decision.dhash_hex,
            hamming_distance=gate_decision.hamming_distance,
        )
        worker.latest_gate_records.append(gate_record)
        logger.info(
            "VISION_GATE_DECISION session=%s frame=%s accepted=%s reason=%s hamming_distance=%s dhash=%s",
            pending_frame.frame_context.session_id,
            pending_frame.frame_context.frame_id,
            gate_decision.accepted,
            gate_decision.reason,
            gate_decision.hamming_distance,
            gate_decision.dhash_hex,
        )
        if not gate_decision.accepted:
            self.storage.update_vision_frame_processing(
                session_id=pending_frame.frame_context.session_id,
                frame_id=pending_frame.frame_context.frame_id,
                processing_status="gated_rejected",
                gate_status="rejected",
                gate_reason=gate_decision.reason,
                phash=gate_decision.dhash_hex,
                provider=self.provider_name,
                model=self.model_name,
            )
            self._cleanup_ingest_artifacts(
                session_id=pending_frame.frame_context.session_id,
                frame_id=pending_frame.frame_context.frame_id,
            )
            return

        self.storage.update_vision_frame_processing(
            session_id=pending_frame.frame_context.session_id,
            frame_id=pending_frame.frame_context.frame_id,
            processing_status="analyzing",
            gate_status="accepted",
            gate_reason=gate_decision.reason,
            phash=gate_decision.dhash_hex,
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
            self.storage.update_vision_frame_processing(
                session_id=pending_frame.frame_context.session_id,
                frame_id=pending_frame.frame_context.frame_id,
                processing_status="analysis_failed",
                gate_status="accepted",
                gate_reason=gate_decision.reason,
                phash=gate_decision.dhash_hex,
                provider=self.provider_name,
                model=self.model_name,
                analyzed_at_ms=now_ms(),
                error_code="VISION_ANALYSIS_FAILED",
            )
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

        worker.last_accepted_frame = AcceptedFrameReference(
            capture_ts_ms=pending_frame.frame_context.capture_ts_ms,
            dhash_hex=gate_decision.dhash_hex,
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
            gate_reason=gate_decision.reason,
            phash=gate_decision.dhash_hex,
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

    def _cleanup_ingest_artifacts(self, *, session_id: str, frame_id: str) -> None:
        if self.settings.vision_debug_retain_raw_frames:
            return
        self.storage.delete_vision_ingest_artifacts(
            session_id=session_id,
            frame_id=frame_id,
        )


def _coerce_optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
