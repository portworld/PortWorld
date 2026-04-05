from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from backend.core.storage import (
    ArtifactRecord,
    BackendStorage,
    MemoryExportArtifact,
    SessionMemoryResetEligibility,
    SessionMemoryResetResult,
    SessionMemoryRetentionEligibility,
    SessionStorageResult,
    VisionFrameIndexRecord,
    VisionFrameIngestResult,
)
from backend.infrastructure.storage.artifacts import ArtifactStorageMixin
from backend.infrastructure.storage.paths import StoragePathMixin
from backend.infrastructure.storage.user_memory import UserMemoryStorageMixin
from backend.infrastructure.storage.sessions import SessionStorageMixin
from backend.infrastructure.storage.sqlite import SQLiteStorageMixin
from backend.infrastructure.storage.types import StorageBootstrapResult, StorageInfo, StoragePaths, now_ms
from backend.infrastructure.storage.vision import VisionFrameStorageMixin

if TYPE_CHECKING:
    from backend.memory.events import AcceptedVisionEvent


class LocalBackendStorage(
    SessionStorageMixin,
    UserMemoryStorageMixin,
    ArtifactStorageMixin,
    VisionFrameStorageMixin,
    SQLiteStorageMixin,
    StoragePathMixin,
    BackendStorage,
):
    """SQLite/filesystem storage implementation used for local mode."""

    # Explicit forwarding methods make the intended MRO resolution visible to
    # static analysis without changing the concrete mixin implementation used.
    def bootstrap_session_storage(self, *, session_id: str) -> SessionStorageResult:
        return super().bootstrap_session_storage(session_id=session_id)

    def ensure_session_storage(self, *, session_id: str) -> SessionStorageResult:
        return super().ensure_session_storage(session_id=session_id)

    def get_session_storage_paths(self, *, session_id: str) -> SessionStorageResult:
        return super().get_session_storage_paths(session_id=session_id)

    def upsert_session_status(self, *, session_id: str, status: str) -> None:
        super().upsert_session_status(session_id=session_id, status=status)

    def append_vision_event(self, *, session_id: str, event: dict[str, Any]) -> None:
        super().append_vision_event(session_id=session_id, event=event)

    def append_vision_routing_event(self, *, session_id: str, event: dict[str, Any]) -> None:
        super().append_vision_routing_event(session_id=session_id, event=event)

    def read_vision_events(self, *, session_id: str) -> list["AcceptedVisionEvent"]:
        return super().read_vision_events(session_id=session_id)

    def read_session_memory(self, *, session_id: str) -> dict[str, Any]:
        return super().read_session_memory(session_id=session_id)

    def read_short_term_memory(self, *, session_id: str) -> dict[str, Any]:
        return super().read_short_term_memory(session_id=session_id)

    def read_session_memory_markdown(self, *, session_id: str) -> str:
        return super().read_session_memory_markdown(session_id=session_id)

    def read_short_term_memory_markdown(self, *, session_id: str) -> str:
        return super().read_short_term_memory_markdown(session_id=session_id)

    def get_session_memory_reset_eligibility(
        self,
        *,
        session_id: str,
    ) -> SessionMemoryResetEligibility:
        return super().get_session_memory_reset_eligibility(session_id=session_id)

    def reset_session_memory(self, *, session_id: str) -> SessionMemoryResetResult:
        return super().reset_session_memory(session_id=session_id)

    def list_session_memory_retention_eligibility(
        self,
        *,
        retention_days: int,
        reference_time_ms: int | None = None,
    ) -> list[SessionMemoryRetentionEligibility]:
        return super().list_session_memory_retention_eligibility(
            retention_days=retention_days,
            reference_time_ms=reference_time_ms,
        )

    def sweep_expired_session_memory(
        self,
        *,
        retention_days: int,
        reference_time_ms: int | None = None,
    ) -> list[SessionMemoryResetResult]:
        return super().sweep_expired_session_memory(
            retention_days=retention_days,
            reference_time_ms=reference_time_ms,
        )

    def write_short_term_memory(
        self,
        *,
        session_id: str,
        payload: dict[str, Any],
        markdown_text: str,
    ) -> None:
        super().write_short_term_memory(
            session_id=session_id,
            payload=payload,
            markdown_text=markdown_text,
        )

    def write_session_memory(
        self,
        *,
        session_id: str,
        payload: dict[str, Any],
        markdown_text: str,
    ) -> None:
        super().write_session_memory(
            session_id=session_id,
            payload=payload,
            markdown_text=markdown_text,
        )

    def read_session_memory_status(self, *, session_id: str) -> dict[str, object]:
        return super().read_session_memory_status(session_id=session_id)

    def register_artifact(
        self,
        *,
        artifact_id: str,
        session_id: str | None,
        artifact_kind: str,
        artifact_path: Any,
        content_type: str,
        metadata: dict[str, Any],
    ) -> ArtifactRecord:
        return super().register_artifact(
            artifact_id=artifact_id,
            session_id=session_id,
            artifact_kind=artifact_kind,
            artifact_path=artifact_path,
            content_type=content_type,
            metadata=metadata,
        )

    def list_memory_export_artifacts(self) -> list[MemoryExportArtifact]:
        return super().list_memory_export_artifacts()

    def read_cross_session_memory(self) -> str:
        return super().read_cross_session_memory()

    def read_user_memory_payload(self) -> dict[str, object]:
        return super().read_user_memory_payload()

    def read_user_memory_markdown(self) -> str:
        return super().read_user_memory_markdown()

    def write_user_memory_payload(
        self,
        *,
        payload: Mapping[str, object],
        source: str | None = None,
        updated_at_ms: int | None = None,
    ) -> dict[str, object]:
        return super().write_user_memory_payload(
            payload=payload,
            source=source,
            updated_at_ms=updated_at_ms,
        )

    def reset_user_memory_payload(self) -> dict[str, object]:
        return super().reset_user_memory_payload()

    def write_user_memory(self, *, markdown: str) -> None:
        super().write_user_memory(markdown=markdown)

    def write_cross_session_memory(self, *, markdown: str) -> None:
        super().write_cross_session_memory(markdown=markdown)

    def append_memory_candidate(self, *, session_id: str, candidate: dict[str, Any]) -> None:
        super().append_memory_candidate(session_id=session_id, candidate=candidate)

    def read_memory_candidates(self, *, session_id: str) -> list[dict[str, Any]]:
        return super().read_memory_candidates(session_id=session_id)

    def store_vision_frame_ingest(
        self,
        *,
        session_id: str,
        frame_id: str,
        ts_ms: int,
        capture_ts_ms: int,
        width: int,
        height: int,
        frame_bytes: bytes,
    ) -> VisionFrameIngestResult:
        return super().store_vision_frame_ingest(
            session_id=session_id,
            frame_id=frame_id,
            ts_ms=ts_ms,
            capture_ts_ms=capture_ts_ms,
            width=width,
            height=height,
            frame_bytes=frame_bytes,
        )

    def delete_vision_ingest_artifacts(self, *, session_id: str, frame_id: str) -> None:
        super().delete_vision_ingest_artifacts(session_id=session_id, frame_id=frame_id)

    def update_vision_frame_processing(
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
        next_retry_at_ms: int | object = None,
        attempt_count: int | object = None,
        error_code: str | None = None,
        error_details: dict[str, Any] | None | object = None,
        summary_snippet: str | None = None,
        routing_status: str | None = None,
        routing_reason: str | None = None,
        routing_score: float | None = None,
        routing_metadata: dict[str, Any] | None = None,
    ) -> None:
        super().update_vision_frame_processing(
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

    def get_vision_frame_record(
        self,
        *,
        session_id: str,
        frame_id: str,
    ) -> VisionFrameIndexRecord | None:
        return super().get_vision_frame_record(session_id=session_id, frame_id=frame_id)

    def __init__(self, *, paths: StoragePaths) -> None:
        self.paths = paths
        super().__init__(
            storage_info=StorageInfo(
                backend="local",
                details={
                    "data_root": str(paths.data_root),
                    "memory_root": str(paths.memory_root),
                    "user_root": str(paths.user_root),
                    "session_root": str(paths.session_root),
                    "vision_frames_root": str(paths.vision_frames_root),
                    "sqlite_path": str(paths.sqlite_path),
                    "user_memory_path": str(paths.user_memory_path),
                    "cross_session_memory_path": str(paths.cross_session_memory_path),
                    "user_profile_markdown_path": str(paths.user_profile_markdown_path),
                },
            )
        )

    def bootstrap(self) -> StorageBootstrapResult:
        self._ensure_directories()
        self._ensure_user_memory_files()
        self._initialize_sqlite()
        return StorageBootstrapResult(
            storage_backend=self.backend_name,
            sqlite_path=self.paths.sqlite_path,
            user_profile_markdown_path=self.paths.user_memory_path,
            bootstrapped_at_ms=now_ms(),
            storage_details=dict(self.storage_info.details),
        )

    def local_storage_paths(self) -> StoragePaths:
        return self.paths
