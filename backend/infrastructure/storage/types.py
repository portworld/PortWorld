from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from time import time_ns
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backend.core.storage import BackendStorage


def now_ms() -> int:
    return time_ns() // 1_000_000


@dataclass(frozen=True, slots=True)
class StoragePaths:
    data_root: Path
    memory_root: Path
    user_root: Path
    session_root: Path
    vision_frames_root: Path
    sqlite_path: Path
    user_memory_path: Path
    cross_session_memory_path: Path
    user_profile_markdown_path: Path

    def to_dict(self) -> dict[str, str]:
        return {
            "data_root": str(self.data_root),
            "memory_root": str(self.memory_root),
            "user_root": str(self.user_root),
            "session_root": str(self.session_root),
            "vision_frames_root": str(self.vision_frames_root),
            "sqlite_path": str(self.sqlite_path),
            "user_memory_path": str(self.user_memory_path),
            "cross_session_memory_path": str(self.cross_session_memory_path),
            "user_profile_markdown_path": str(self.user_profile_markdown_path),
        }


@dataclass(frozen=True, slots=True)
class StorageInfo:
    backend: str
    details: dict[str, str | bool]

    def to_dict(self) -> dict[str, object]:
        return {
            "backend": self.backend,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class StorageBootstrapResult:
    storage_backend: str
    sqlite_path: Path | None
    user_profile_markdown_path: Path | None
    bootstrapped_at_ms: int
    storage_details: dict[str, str | bool]

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "storage_backend": self.storage_backend,
            "bootstrapped_at_ms": self.bootstrapped_at_ms,
            "storage_details": dict(self.storage_details),
        }
        if self.sqlite_path is not None:
            payload["sqlite_path"] = str(self.sqlite_path)
        if self.user_profile_markdown_path is not None:
            payload["user_profile_markdown_path"] = str(self.user_profile_markdown_path)
        return payload


@dataclass(frozen=True, slots=True)
class SessionStorageResult:
    session_dir: Path
    short_term_memory_markdown_path: Path
    session_memory_markdown_path: Path
    session_memory_json_path: Path
    memory_candidates_log_path: Path
    vision_events_log_path: Path
    vision_routing_events_log_path: Path


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    artifact_id: str
    session_id: str | None
    artifact_kind: str
    relative_path: str
    content_type: str
    metadata_json: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class VisionFrameIndexRecord:
    session_id: str
    frame_id: str
    capture_ts_ms: int
    ingest_ts_ms: int
    width: int
    height: int
    processing_status: str
    gate_status: str | None
    gate_reason: str | None
    phash: str | None
    provider: str | None
    model: str | None
    analyzed_at_ms: int | None
    next_retry_at_ms: int | None
    attempt_count: int
    error_code: str | None
    error_details_json: str | None
    summary_snippet: str | None
    routing_status: str | None
    routing_reason: str | None
    routing_score: float | None
    routing_metadata_json: str | None


@dataclass(frozen=True, slots=True)
class MemoryExportArtifact:
    artifact_id: str | None
    session_id: str | None
    artifact_kind: str
    relative_path: str
    content_type: str
    created_at_ms: int | None
    read_bytes: Callable[[], bytes] = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class SessionMemoryResetResult:
    session_id: str
    deleted_artifact_rows: int
    deleted_vision_frame_rows: int
    deleted_session_rows: int
    removed_session_dir: bool
    removed_vision_frames_dir: bool


@dataclass(frozen=True, slots=True)
class VisionFrameIngestResult:
    frame_path: Path
    metadata_path: Path
    stored_bytes: int


@dataclass(frozen=True, slots=True)
class RealtimeReadOnlyStorageView:
    _storage: "BackendStorage"

    def read_short_term_memory(self, *, session_id: str) -> dict[str, Any]:
        return self._storage.read_short_term_memory(session_id=session_id)

    def read_session_memory(self, *, session_id: str) -> dict[str, Any]:
        return self._storage.read_session_memory(session_id=session_id)

    def read_short_term_memory_markdown(self, *, session_id: str) -> str:
        return self._storage.read_short_term_memory_markdown(session_id=session_id)

    def read_session_memory_markdown(self, *, session_id: str) -> str:
        return self._storage.read_session_memory_markdown(session_id=session_id)

    def read_user_memory_payload(self) -> dict[str, Any]:
        return self._storage.read_user_memory_payload()

    def read_user_memory(self) -> str:
        return self._storage.read_user_memory()

    def read_cross_session_memory(self) -> str:
        return self._storage.read_cross_session_memory()
