from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from backend.infrastructure.storage import (
    ArtifactRecord,
    CorruptStorageArtifactError,
    MemoryExportArtifact,
    RealtimeReadOnlyStorageView,
    SessionNotFoundError,
    SessionMemoryResetResult,
    SessionStorageResult,
    StorageBootstrapResult,
    StorageInfo,
    StoragePaths,
    VisionFrameIndexRecord,
    VisionFrameIngestResult,
    now_ms,
)
from backend.memory.lifecycle import (
    ProfileRecord,
    UserMemoryRecord,
    SessionMemoryResetEligibility,
    SessionMemoryRetentionEligibility,
)

if TYPE_CHECKING:
    from backend.memory.events import AcceptedVisionEvent


class BackendStorage:
    """Backend-agnostic storage contract used by runtime and CLI callers."""

    def __init__(self, *, storage_info: StorageInfo) -> None:
        self.storage_info = storage_info

    @property
    def backend_name(self) -> str:
        return self.storage_info.backend

    @property
    def is_local_backend(self) -> bool:
        return self.backend_name == "local"

    def bootstrap(self) -> StorageBootstrapResult:
        raise NotImplementedError

    def realtime_read_only_view(self) -> RealtimeReadOnlyStorageView:
        return RealtimeReadOnlyStorageView(self)

    def local_storage_paths(self) -> StoragePaths:
        raise RuntimeError(
            f"Storage backend {self.backend_name!r} does not expose local filesystem paths."
        )

    def bootstrap_session_storage(self, *, session_id: str) -> SessionStorageResult:
        raise NotImplementedError

    def ensure_session_storage(self, *, session_id: str) -> SessionStorageResult:
        raise NotImplementedError

    def get_session_storage_paths(self, *, session_id: str) -> SessionStorageResult:
        raise NotImplementedError

    def upsert_session_status(self, *, session_id: str, status: str) -> None:
        raise NotImplementedError

    def append_vision_event(self, *, session_id: str, event: dict[str, Any]) -> None:
        raise NotImplementedError

    def append_vision_routing_event(self, *, session_id: str, event: dict[str, Any]) -> None:
        raise NotImplementedError

    def read_vision_events(self, *, session_id: str) -> list["AcceptedVisionEvent"]:
        raise NotImplementedError

    def read_session_memory(self, *, session_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def read_short_term_memory(self, *, session_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def read_session_memory_markdown(self, *, session_id: str) -> str:
        raise NotImplementedError

    def read_short_term_memory_markdown(self, *, session_id: str) -> str:
        raise NotImplementedError

    def get_session_memory_reset_eligibility(
        self,
        *,
        session_id: str,
    ) -> SessionMemoryResetEligibility:
        raise NotImplementedError

    def reset_session_memory(self, *, session_id: str) -> SessionMemoryResetResult:
        raise NotImplementedError

    def list_session_memory_retention_eligibility(
        self,
        *,
        retention_days: int,
        reference_time_ms: int | None = None,
    ) -> list[SessionMemoryRetentionEligibility]:
        raise NotImplementedError

    def sweep_expired_session_memory(
        self,
        *,
        retention_days: int,
        reference_time_ms: int | None = None,
    ) -> list[SessionMemoryResetResult]:
        raise NotImplementedError

    def write_short_term_memory(
        self,
        *,
        session_id: str,
        payload: dict[str, Any],
        markdown_text: str,
    ) -> None:
        raise NotImplementedError

    def write_session_memory(
        self,
        *,
        session_id: str,
        payload: dict[str, Any],
        markdown_text: str,
    ) -> None:
        raise NotImplementedError

    def read_session_memory_status(self, *, session_id: str) -> dict[str, object]:
        raise NotImplementedError

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
        raise NotImplementedError

    def list_memory_export_artifacts(self) -> list[MemoryExportArtifact]:
        raise NotImplementedError

    def read_user_memory(self) -> str:
        return self.read_user_memory_markdown()

    def read_cross_session_memory(self) -> str:
        raise NotImplementedError

    def read_user_memory_payload(self) -> dict[str, object]:
        raise NotImplementedError

    def read_user_memory_markdown(self) -> str:
        raise NotImplementedError

    def write_user_memory_payload(
        self,
        *,
        payload: Mapping[str, object],
        source: str | None = None,
        updated_at_ms: int | None = None,
    ) -> dict[str, object]:
        raise NotImplementedError

    def reset_user_memory_payload(self) -> dict[str, object]:
        raise NotImplementedError

    def read_user_profile(self) -> dict[str, object]:
        return self.read_user_memory_payload()

    def read_user_profile_markdown(self) -> str:
        return self.read_user_memory_markdown()

    def write_user_profile(
        self,
        *,
        payload: Mapping[str, object],
        source: str | None = None,
        updated_at_ms: int | None = None,
    ) -> dict[str, object]:
        return self.write_user_memory_payload(
            payload=payload,
            source=source,
            updated_at_ms=updated_at_ms,
        )

    def reset_user_profile(self) -> dict[str, object]:
        return self.reset_user_memory_payload()

    def write_user_memory(self, *, markdown: str) -> None:
        raise NotImplementedError

    def write_cross_session_memory(self, *, markdown: str) -> None:
        raise NotImplementedError

    def append_memory_candidate(self, *, session_id: str, candidate: dict[str, Any]) -> None:
        raise NotImplementedError

    def read_memory_candidates(self, *, session_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

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
        raise NotImplementedError

    def delete_vision_ingest_artifacts(self, *, session_id: str, frame_id: str) -> None:
        raise NotImplementedError

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
        raise NotImplementedError

    def get_vision_frame_record(
        self,
        *,
        session_id: str,
        frame_id: str,
    ) -> VisionFrameIndexRecord | None:
        raise NotImplementedError

__all__ = [
    "ArtifactRecord",
    "BackendStorage",
    "CorruptStorageArtifactError",
    "MemoryExportArtifact",
    "ProfileRecord",
    "UserMemoryRecord",
    "RealtimeReadOnlyStorageView",
    "SessionMemoryResetEligibility",
    "SessionMemoryResetResult",
    "SessionMemoryRetentionEligibility",
    "SessionNotFoundError",
    "SessionStorageResult",
    "StorageBootstrapResult",
    "StorageInfo",
    "StoragePaths",
    "VisionFrameIndexRecord",
    "VisionFrameIngestResult",
    "now_ms",
]
