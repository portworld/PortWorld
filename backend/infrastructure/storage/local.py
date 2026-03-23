from __future__ import annotations

from backend.core.storage import BackendStorage
from backend.infrastructure.storage.artifacts import ArtifactStorageMixin
from backend.infrastructure.storage.paths import StoragePathMixin
from backend.infrastructure.storage.user_memory import UserMemoryStorageMixin
from backend.infrastructure.storage.sessions import SessionStorageMixin
from backend.infrastructure.storage.sqlite import SQLiteStorageMixin
from backend.infrastructure.storage.types import StorageBootstrapResult, StorageInfo, StoragePaths, now_ms
from backend.infrastructure.storage.vision import VisionFrameStorageMixin


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
                    "user_profile_json_path": str(paths.user_profile_json_path),
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
            user_profile_json_path=None,
            bootstrapped_at_ms=now_ms(),
            storage_details=dict(self.storage_info.details),
        )

    def local_storage_paths(self) -> StoragePaths:
        return self.paths
