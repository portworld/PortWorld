from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.core.settings import Settings
from backend.realtime.factory import BridgeBinding, build_session_bridge


@dataclass(frozen=True, slots=True)
class RuntimeStoragePaths:
    data_root: Path
    vision_frames_root: Path
    debug_audio_root: Path


@dataclass(frozen=True, slots=True)
class AppRuntime:
    settings: Settings
    storage_paths: RuntimeStoragePaths

    @classmethod
    def from_env(cls) -> "AppRuntime":
        return cls.from_settings(Settings.from_env())

    @classmethod
    def from_settings(cls, settings: Settings) -> "AppRuntime":
        return cls(
            settings=settings,
            storage_paths=RuntimeStoragePaths(
                data_root=settings.backend_data_dir,
                vision_frames_root=settings.backend_data_dir / "vision_frames",
                debug_audio_root=settings.backend_debug_dump_input_audio_dir,
            ),
        )

    def make_session_bridge(
        self,
        *,
        session_id: str,
        send_control: Any,
        send_server_audio: Any,
    ) -> BridgeBinding:
        return build_session_bridge(
            settings=self.settings,
            session_id=session_id,
            send_control=send_control,
            send_server_audio=send_server_audio,
        )


def get_app_runtime(app: Any) -> AppRuntime:
    runtime = getattr(getattr(app, "state", None), "runtime", None)
    if isinstance(runtime, AppRuntime):
        return runtime
    raise RuntimeError("App runtime is not initialized")
