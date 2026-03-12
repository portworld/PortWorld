from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.core.settings import Settings
from backend.core.storage import BackendStorage, StoragePaths
from backend.realtime.factory import RealtimeProviderFactory
from backend.tools.runtime import RealtimeToolingRuntime
from backend.vision.factory import VisionAnalyzerFactory
from backend.vision.runtime import VisionBudgetManager, VisionMemoryRuntime


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

    def to_dict(self) -> dict[str, str]:
        return {
            "data_root": str(self.data_root),
            "user_root": str(self.user_root),
            "session_root": str(self.session_root),
            "vision_frames_root": str(self.vision_frames_root),
            "debug_audio_root": str(self.debug_audio_root),
            "sqlite_path": str(self.sqlite_path),
            "user_profile_markdown_path": str(self.user_profile_markdown_path),
            "user_profile_json_path": str(self.user_profile_json_path),
        }


@dataclass(frozen=True, slots=True)
class RuntimeDependencies:
    storage_paths: RuntimeStoragePaths
    storage: BackendStorage
    realtime_provider_factory: RealtimeProviderFactory
    vision_memory_runtime: VisionMemoryRuntime | None
    realtime_tooling_runtime: RealtimeToolingRuntime | None


@dataclass(frozen=True, slots=True)
class ConfigCheckResult:
    ok: bool
    storage_paths: dict[str, str]
    realtime_provider: str
    vision_provider: str | None
    realtime_tooling_enabled: bool
    web_search_provider: str | None
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "storage_paths": self.storage_paths,
            "realtime_provider": self.realtime_provider,
            "vision_provider": self.vision_provider,
            "realtime_tooling_enabled": self.realtime_tooling_enabled,
            "web_search_provider": self.web_search_provider,
            "warnings": list(self.warnings),
        }


def build_backend_storage(settings: Settings) -> tuple[RuntimeStoragePaths, BackendStorage]:
    storage_paths = build_runtime_storage_paths(settings)
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
    return storage_paths, storage


def build_runtime_storage_paths(settings: Settings) -> RuntimeStoragePaths:
    return RuntimeStoragePaths(
        data_root=settings.backend_data_dir,
        user_root=settings.backend_data_dir / "user",
        session_root=settings.backend_data_dir / "session",
        vision_frames_root=settings.backend_data_dir / "vision_frames",
        debug_audio_root=settings.backend_debug_dump_input_audio_dir,
        sqlite_path=settings.backend_sqlite_path,
        user_profile_markdown_path=settings.backend_data_dir / "user" / "user_profile.md",
        user_profile_json_path=settings.backend_data_dir / "user" / "user_profile.json",
    )


def build_runtime_dependencies(settings: Settings) -> RuntimeDependencies:
    settings.validate_production_posture()
    storage_paths, storage = build_backend_storage(settings)
    realtime_provider_factory = RealtimeProviderFactory(settings=settings)
    realtime_provider_factory.validate_startup_configuration()

    vision_memory_runtime = None
    if settings.vision_memory_enabled:
        analyzer_factory = VisionAnalyzerFactory(settings=settings)
        analyzer_factory.validate_configuration()
        vision_memory_runtime = VisionMemoryRuntime(
            settings=settings,
            storage=storage,
            analyzer=analyzer_factory.build_analyzer(),
            provider_budget=VisionBudgetManager(
                max_rps=settings.vision_provider_max_rps,
                backoff_initial_seconds=settings.vision_provider_backoff_initial_seconds,
                backoff_max_seconds=settings.vision_provider_backoff_max_seconds,
            ),
        )

    realtime_tooling_runtime = None
    if settings.realtime_tooling_enabled:
        realtime_tooling_runtime = RealtimeToolingRuntime.from_settings(
            settings,
            storage=storage,
        )

    return RuntimeDependencies(
        storage_paths=storage_paths,
        storage=storage,
        realtime_provider_factory=realtime_provider_factory,
        vision_memory_runtime=vision_memory_runtime,
        realtime_tooling_runtime=realtime_tooling_runtime,
    )


def check_runtime_configuration(settings: Settings) -> ConfigCheckResult:
    dependencies = build_runtime_dependencies(settings)
    warnings: list[str] = []

    dependencies.realtime_provider_factory.validate_configuration()

    vision_provider = None
    if settings.vision_memory_enabled:
        vision_factory = VisionAnalyzerFactory(settings=settings)
        vision_factory.validate_configuration()
        vision_provider = vision_factory.provider_name

    web_search_provider = None
    if settings.realtime_tooling_enabled:
        tooling_runtime = dependencies.realtime_tooling_runtime
        assert tooling_runtime is not None
        web_search_provider = tooling_runtime.web_search_provider
        if not tooling_runtime.web_search_enabled:
            warnings.append(
                "REALTIME_TOOLING_ENABLED is true but web_search is disabled because the configured search provider is not enabled by current credentials."
            )

    return ConfigCheckResult(
        ok=True,
        storage_paths=dependencies.storage_paths.to_dict(),
        realtime_provider=dependencies.realtime_provider_factory.provider_name,
        vision_provider=vision_provider,
        realtime_tooling_enabled=settings.realtime_tooling_enabled,
        web_search_provider=web_search_provider,
        warnings=tuple(warnings),
    )
