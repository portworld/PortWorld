from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field

from backend.core.settings import Settings
from backend.core.storage import BackendStorage, now_ms
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
    pending_frame: PendingVisionFrame | None = None
    latest_gate_records: deque[GateRecord] = field(default_factory=lambda: deque(maxlen=25))
    last_accepted_frame: AcceptedFrameReference | None = None
    last_observation: VisionObservation | None = None
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
        self._shutdown_requested = True
        async with self._workers_lock:
            workers = list(self._workers.values())
            self._workers.clear()
        for worker in workers:
            async with worker.condition:
                worker.pending_frame = None
                worker.condition.notify_all()
            if worker.task is not None:
                worker.task.cancel()
        if workers:
            await asyncio.gather(
                *(worker.task for worker in workers if worker.task is not None),
                return_exceptions=True,
            )
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

    async def _ensure_worker(self, *, session_id: str) -> SessionVisionWorker:
        async with self._workers_lock:
            worker = self._workers.get(session_id)
            if worker is None:
                worker = SessionVisionWorker(session_id=session_id)
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
                    if self._shutdown_requested:
                        break
                    continue
                await self._process_pending_frame(worker, pending_frame)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("VISION_SESSION_WORKER_FAILED session=%s", worker.session_id)

    async def _wait_for_pending_frame(self, worker: SessionVisionWorker) -> PendingVisionFrame | None:
        async with worker.condition:
            while worker.pending_frame is None and not self._shutdown_requested:
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
            return

        worker.last_accepted_frame = AcceptedFrameReference(
            capture_ts_ms=pending_frame.frame_context.capture_ts_ms,
            dhash_hex=gate_decision.dhash_hex,
        )
        worker.last_observation = observation
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
