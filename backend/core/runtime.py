from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from backend.core.settings import Settings
from backend.core.storage import BackendStorage, StorageBootstrapResult, StoragePaths
from backend.realtime.factory import RealtimeProviderFactory, build_debug_mock_capture_bridge
from backend.tools.runtime import RealtimeToolingRuntime
from backend.vision.runtime import VisionMemoryRuntime

if TYPE_CHECKING:
    from backend.realtime.factory import BridgeBinding


@dataclass(frozen=True, slots=True)
class RuntimeStoragePaths:
    data_root: Path
    user_root: Path
    session_root: Path
    vision_frames_root: Path
    debug_audio_root: Path
    sqlite_path: Path
    user_profile_markdown_path: Path
    user_profile_json_path: Path


@dataclass(frozen=True, slots=True)
class AppRuntime:
    settings: Settings
    storage_paths: RuntimeStoragePaths
    storage: BackendStorage
    realtime_provider: RealtimeProviderFactory
    vision_memory_runtime: VisionMemoryRuntime | None
    realtime_tooling_runtime: RealtimeToolingRuntime | None
    storage_bootstrap_result: StorageBootstrapResult | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    @classmethod
    def from_env(cls) -> "AppRuntime":
        return cls.from_settings(Settings.from_env())

    @classmethod
    def from_settings(cls, settings: Settings) -> "AppRuntime":
        storage_paths = RuntimeStoragePaths(
            data_root=settings.backend_data_dir,
            user_root=settings.backend_data_dir / "user",
            session_root=settings.backend_data_dir / "session",
            vision_frames_root=settings.backend_data_dir / "vision_frames",
            debug_audio_root=settings.backend_debug_dump_input_audio_dir,
            sqlite_path=settings.backend_sqlite_path,
            user_profile_markdown_path=settings.backend_data_dir / "user" / "user_profile.md",
            user_profile_json_path=settings.backend_data_dir / "user" / "user_profile.json",
        )
        storage = BackendStorage(
            paths=StoragePaths(
                data_root=storage_paths.data_root,
                user_root=storage_paths.user_root,
                session_root=storage_paths.session_root,
                vision_frames_root=storage_paths.vision_frames_root,
                debug_audio_root=storage_paths.debug_audio_root,
                sqlite_path=storage_paths.sqlite_path,
                user_profile_markdown_path=storage_paths.user_profile_markdown_path,
                user_profile_json_path=storage_paths.user_profile_json_path,
            )
        )
        vision_memory_runtime = None
        if settings.vision_memory_enabled:
            vision_memory_runtime = VisionMemoryRuntime.from_settings(settings, storage=storage)
        realtime_tooling_runtime = None
        if settings.realtime_tooling_enabled:
            realtime_tooling_runtime = RealtimeToolingRuntime.from_settings(
                settings,
                storage=storage,
            )
        return cls(
            settings=settings,
            storage_paths=storage_paths,
            storage=storage,
            realtime_provider=RealtimeProviderFactory(settings=settings),
            vision_memory_runtime=vision_memory_runtime,
            realtime_tooling_runtime=realtime_tooling_runtime,
        )

    def bootstrap_storage(self) -> StorageBootstrapResult:
        result = self.storage.bootstrap()
        object.__setattr__(self, "storage_bootstrap_result", result)
        return result

    async def startup(self) -> None:
        self.bootstrap_storage()
        if self.vision_memory_runtime is not None:
            await self.vision_memory_runtime.startup()
        if self.realtime_tooling_runtime is not None:
            await self.realtime_tooling_runtime.startup()

    async def shutdown(self) -> None:
        if self.realtime_tooling_runtime is not None:
            await self.realtime_tooling_runtime.shutdown()
        if self.vision_memory_runtime is not None:
            await self.vision_memory_runtime.shutdown()

    def make_session_bridge(
        self,
        *,
        session_id: str,
        send_control: Any,
        send_server_audio: Any,
    ) -> "BridgeBinding":
        if self.settings.backend_debug_mock_capture_mode:
            return build_debug_mock_capture_bridge(
                settings=self.settings,
                session_id=session_id,
            )
        return self.realtime_provider.build_session_bridge(
            session_id=session_id,
            send_control=send_control,
            send_server_audio=send_server_audio,
            realtime_tooling_runtime=self.realtime_tooling_runtime,
        )


def get_app_runtime(app: Any) -> AppRuntime:
    runtime = getattr(getattr(app, "state", None), "runtime", None)
    if isinstance(runtime, AppRuntime):
        return runtime
    raise RuntimeError("App runtime is not initialized")
