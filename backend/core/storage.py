from __future__ import annotations

from backend.infrastructure.storage import (
    ArtifactRecord,
    ArtifactStorageMixin,
    CorruptStorageArtifactError,
    MemoryExportArtifact,
    ProfileStorageMixin,
    RealtimeReadOnlyStorageView,
    SessionNotFoundError,
    SessionMemoryResetResult,
    SessionStorageMixin,
    SessionStorageResult,
    SQLiteStorageMixin,
    StorageBootstrapResult,
    StoragePathMixin,
    StoragePaths,
    VisionFrameIndexRecord,
    VisionFrameIngestResult,
    VisionFrameStorageMixin,
    now_ms,
)
from backend.memory.lifecycle import (
    ProfileRecord,
    SessionMemoryResetEligibility,
    SessionMemoryRetentionEligibility,
)


class BackendStorage(
    SessionStorageMixin,
    ProfileStorageMixin,
    ArtifactStorageMixin,
    VisionFrameStorageMixin,
    SQLiteStorageMixin,
    StoragePathMixin,
):
    def __init__(self, *, paths: StoragePaths) -> None:
        self.paths = paths

    def bootstrap(self) -> StorageBootstrapResult:
        self._ensure_directories()
        self._ensure_user_profile_files()
        self._initialize_sqlite()
        return StorageBootstrapResult(
            sqlite_path=self.paths.sqlite_path,
            user_profile_markdown_path=self.paths.user_profile_markdown_path,
            user_profile_json_path=self.paths.user_profile_json_path,
            bootstrapped_at_ms=now_ms(),
        )

    def realtime_read_only_view(self) -> RealtimeReadOnlyStorageView:
        return RealtimeReadOnlyStorageView(self)


__all__ = [
    "ArtifactRecord",
    "BackendStorage",
    "CorruptStorageArtifactError",
    "MemoryExportArtifact",
    "ProfileRecord",
    "RealtimeReadOnlyStorageView",
    "SessionMemoryResetEligibility",
    "SessionMemoryResetResult",
    "SessionMemoryRetentionEligibility",
    "SessionNotFoundError",
    "SessionStorageResult",
    "StorageBootstrapResult",
    "StoragePaths",
    "VisionFrameIndexRecord",
    "VisionFrameIngestResult",
    "now_ms",
]
