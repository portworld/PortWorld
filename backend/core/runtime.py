from __future__ import annotations

import logging
from dataclasses import dataclass, field
from hashlib import sha1
from pathlib import Path
from typing import TYPE_CHECKING, Any

from backend.core.rate_limit import RateLimitDecision, SlidingWindowRateLimiter
from backend.core.settings import Settings
from backend.core.storage import BackendStorage, StorageBootstrapResult, StoragePaths
from backend.realtime.factory import RealtimeProviderFactory, build_debug_mock_capture_bridge
from backend.tools.runtime import RealtimeToolingRuntime
from backend.vision.runtime import VisionMemoryRuntime

if TYPE_CHECKING:
    from backend.realtime.factory import BridgeBinding


logger = logging.getLogger(__name__)


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
    rate_limiter: SlidingWindowRateLimiter
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
        settings.validate_production_posture()
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
            rate_limiter=SlidingWindowRateLimiter(),
        )

    def bootstrap_storage(self) -> StorageBootstrapResult:
        result = self.storage.bootstrap()
        object.__setattr__(self, "storage_bootstrap_result", result)
        return result

    async def startup(self) -> None:
        self.bootstrap_storage()
        self._sweep_expired_session_memory_at_startup()
        if self.vision_memory_runtime is not None:
            await self.vision_memory_runtime.startup()
        if self.realtime_tooling_runtime is not None:
            await self.realtime_tooling_runtime.startup()

    async def shutdown(self) -> None:
        if self.realtime_tooling_runtime is not None:
            await self.realtime_tooling_runtime.shutdown()
        if self.vision_memory_runtime is not None:
            await self.vision_memory_runtime.shutdown()

    async def limit_ws_connect(self, *, client_ip: str) -> RateLimitDecision:
        if not self.settings.backend_enable_ip_rate_limits:
            return RateLimitDecision(allowed=True, scope="ip_disabled")
        ip_key = _rate_key(client_ip)
        return await self.rate_limiter.allow(
            key=f"ws_connect:ip:{ip_key}",
            limit=self.settings.backend_rate_limit_ws_ip_max_attempts,
            window_seconds=self.settings.backend_rate_limit_ws_window_seconds,
            scope="ip",
        )

    async def limit_ws_session_activation(
        self,
        *,
        client_ip: str,
        session_id: str,
    ) -> RateLimitDecision:
        session_key = _rate_key(session_id)
        if self.settings.backend_enable_ip_rate_limits:
            ip_key = _rate_key(client_ip)
            ip_decision = await self.rate_limiter.allow(
                key=f"ws_activate:ip:{ip_key}",
                limit=self.settings.backend_rate_limit_ws_ip_max_attempts,
                window_seconds=self.settings.backend_rate_limit_ws_window_seconds,
                scope="ip",
            )
            if not ip_decision.allowed:
                return ip_decision
        return await self.rate_limiter.allow(
            key=f"ws_activate:session:{session_key}",
            limit=self.settings.backend_rate_limit_ws_session_max_attempts,
            window_seconds=self.settings.backend_rate_limit_ws_window_seconds,
            scope="session",
        )

    async def limit_vision_frame_ingest(
        self,
        *,
        client_ip: str,
        session_id: str,
    ) -> RateLimitDecision:
        session_key = _rate_key(session_id)
        if self.settings.backend_enable_ip_rate_limits:
            ip_key = _rate_key(client_ip)
            ip_decision = await self.rate_limiter.allow(
                key=f"vision_ingest:ip:{ip_key}",
                limit=self.settings.backend_rate_limit_vision_ip_max_requests,
                window_seconds=self.settings.backend_rate_limit_vision_window_seconds,
                scope="ip",
            )
            if not ip_decision.allowed:
                return ip_decision
        return await self.rate_limiter.allow(
            key=f"vision_ingest:session:{session_key}",
            limit=self.settings.backend_rate_limit_vision_session_max_requests,
            window_seconds=self.settings.backend_rate_limit_vision_window_seconds,
            scope="session",
        )

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

    def _sweep_expired_session_memory_at_startup(self) -> None:
        try:
            expired_sessions = self.storage.sweep_expired_session_memory(
                retention_days=self.settings.backend_session_memory_retention_days,
            )
        except Exception:
            logger.exception(
                "Failed sweeping expired session memory at startup retention_days=%s",
                self.settings.backend_session_memory_retention_days,
            )
            return

        if expired_sessions:
            logger.info(
                "Expired session memory swept at startup count=%s sessions=%s",
                len(expired_sessions),
                [result.session_id for result in expired_sessions],
            )


def get_app_runtime(app: Any) -> AppRuntime:
    runtime = getattr(getattr(app, "state", None), "runtime", None)
    if isinstance(runtime, AppRuntime):
        return runtime
    raise RuntimeError("App runtime is not initialized")


def _rate_key(raw_value: str) -> str:
    normalized = raw_value.strip()
    if not normalized:
        return "unknown"
    if len(normalized) <= 64:
        return normalized
    return sha1(normalized.encode("utf-8")).hexdigest()
