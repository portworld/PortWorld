from __future__ import annotations

from dataclasses import dataclass

from backend.core.settings import Settings
from backend.core.storage import BackendStorage, StorageInfo, StoragePaths
from backend.infrastructure.storage.object_store import build_object_store
from backend.infrastructure.storage.local import LocalBackendStorage
from backend.infrastructure.storage.managed import ManagedBackendStorage
from backend.realtime.factory import RealtimeProviderFactory
from backend.tools.runtime import RealtimeToolingRuntime
from backend.vision.factory import VisionAnalyzerFactory
from backend.vision.runtime import VisionBudgetManager, VisionMemoryRuntime


@dataclass(frozen=True, slots=True)
class RuntimeDependencies:
    storage_info: StorageInfo
    storage: BackendStorage
    realtime_provider_factory: RealtimeProviderFactory
    vision_memory_runtime: VisionMemoryRuntime | None
    realtime_tooling_runtime: RealtimeToolingRuntime | None


@dataclass(frozen=True, slots=True)
class ConfigCheckResult:
    ok: bool
    storage_backend: str
    storage_details: dict[str, str | bool]
    storage_paths: dict[str, str] | None
    realtime_provider: str
    vision_provider: str | None
    realtime_tooling_enabled: bool
    web_search_provider: str | None
    check_mode: str
    storage_bootstrap_probe: bool | None
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "ok": self.ok,
            "storage_backend": self.storage_backend,
            "storage_details": dict(self.storage_details),
            "realtime_provider": self.realtime_provider,
            "vision_provider": self.vision_provider,
            "realtime_tooling_enabled": self.realtime_tooling_enabled,
            "web_search_provider": self.web_search_provider,
            "check_mode": self.check_mode,
            "storage_bootstrap_probe": self.storage_bootstrap_probe,
            "warnings": list(self.warnings),
        }
        if self.storage_paths is not None:
            payload["storage_paths"] = dict(self.storage_paths)
        return payload


@dataclass(frozen=True, slots=True)
class DoctorRuntimeDetails:
    storage_backend: str
    storage_details: dict[str, str | bool]
    storage_paths: dict[str, str] | None
    realtime_provider: str
    vision_provider: str | None
    realtime_tooling_enabled: bool
    web_search_provider: str | None
    web_search_enabled: bool
    storage_bootstrap_probe: bool | None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "storage_backend": self.storage_backend,
            "storage_details": dict(self.storage_details),
            "realtime_provider": self.realtime_provider,
            "vision_provider": self.vision_provider,
            "realtime_tooling_enabled": self.realtime_tooling_enabled,
            "web_search_provider": self.web_search_provider,
            "web_search_enabled": self.web_search_enabled,
            "storage_bootstrap_probe": self.storage_bootstrap_probe,
        }
        if self.storage_paths is not None:
            payload["storage_paths"] = dict(self.storage_paths)
        return payload


def build_backend_storage(settings: Settings) -> tuple[StorageInfo, BackendStorage]:
    settings.validate_storage_contract()
    if settings.backend_storage_backend == "local":
        storage_paths = build_storage_paths(settings)
        storage = LocalBackendStorage(paths=storage_paths)
        return storage.storage_info, storage

    storage = ManagedBackendStorage(
        database_url=settings.backend_database_url or "",
        object_store=build_object_store(
            provider=settings.backend_object_store_provider,
            bucket_name=settings.backend_object_store_bucket or "",
            key_prefix=settings.backend_object_store_prefix or "",
        ),
    )
    return storage.storage_info, storage


def build_storage_paths(settings: Settings) -> StoragePaths:
    if settings.backend_storage_backend != "local":
        raise RuntimeError(
            "Local storage paths are only defined when "
            "BACKEND_STORAGE_BACKEND=local."
        )
    return StoragePaths(
        data_root=settings.backend_data_dir,
        user_root=settings.backend_data_dir / "user",
        session_root=settings.backend_data_dir / "session",
        vision_frames_root=settings.backend_data_dir / "vision_frames",
        sqlite_path=settings.backend_sqlite_path,
        user_profile_markdown_path=settings.backend_data_dir / "user" / "user_profile.md",
        user_profile_json_path=settings.backend_data_dir / "user" / "user_profile.json",
    )


def build_runtime_dependencies(settings: Settings) -> RuntimeDependencies:
    settings.validate_production_posture()
    settings.validate_storage_contract()
    storage_info, storage = build_backend_storage(settings)
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
        storage_info=storage_info,
        storage=storage,
        realtime_provider_factory=realtime_provider_factory,
        vision_memory_runtime=vision_memory_runtime,
        realtime_tooling_runtime=realtime_tooling_runtime,
    )


def check_runtime_configuration(
    settings: Settings,
    *,
    full_readiness: bool = False,
) -> ConfigCheckResult:
    dependencies = build_runtime_dependencies(settings)
    warnings: list[str] = []
    check_mode = "full_readiness" if full_readiness else "basic"
    storage_bootstrap_probe: bool | None = None

    if full_readiness:
        dependencies.storage.bootstrap()
        storage_bootstrap_probe = True

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

    storage_paths = None
    if dependencies.storage.is_local_backend:
        storage_paths = dependencies.storage.local_storage_paths().to_dict()

    return ConfigCheckResult(
        ok=True,
        storage_backend=dependencies.storage_info.backend,
        storage_details=dict(dependencies.storage_info.details),
        storage_paths=storage_paths,
        realtime_provider=dependencies.realtime_provider_factory.provider_name,
        vision_provider=vision_provider,
        realtime_tooling_enabled=settings.realtime_tooling_enabled,
        web_search_provider=web_search_provider,
        check_mode=check_mode,
        storage_bootstrap_probe=storage_bootstrap_probe,
        warnings=tuple(warnings),
    )


def collect_doctor_runtime_details(
    settings: Settings,
    *,
    full_readiness: bool = False,
) -> DoctorRuntimeDetails:
    settings.validate_production_posture()
    storage_info, storage = build_backend_storage(settings)

    realtime_provider_factory = RealtimeProviderFactory(settings=settings)
    realtime_provider_factory.validate_configuration()

    vision_provider = None
    if settings.vision_memory_enabled:
        vision_factory = VisionAnalyzerFactory(settings=settings)
        vision_factory.validate_configuration()
        vision_provider = vision_factory.provider_name

    web_search_provider = None
    web_search_enabled = False
    if settings.realtime_tooling_enabled:
        tooling_runtime = RealtimeToolingRuntime.from_settings(
            settings,
            storage=storage,
        )
        web_search_provider = settings.realtime_web_search_provider
        web_search_enabled = tooling_runtime.web_search_enabled

    storage_bootstrap_probe: bool | None = None
    if full_readiness:
        storage.bootstrap()
        storage_bootstrap_probe = True

    storage_paths = None
    if storage.is_local_backend:
        storage_paths = storage.local_storage_paths().to_dict()

    return DoctorRuntimeDetails(
        storage_backend=storage_info.backend,
        storage_details=dict(storage_info.details),
        storage_paths=storage_paths,
        realtime_provider=realtime_provider_factory.provider_name,
        vision_provider=vision_provider,
        realtime_tooling_enabled=settings.realtime_tooling_enabled,
        web_search_provider=web_search_provider,
        web_search_enabled=web_search_enabled,
        storage_bootstrap_probe=storage_bootstrap_probe,
    )
