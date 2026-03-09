from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from backend.core.settings import Settings
from backend.tools.runtime import RealtimeToolingRuntime
from backend.ws.session_registry import SessionBridge, SessionRecord

ControlSender = Callable[..., Awaitable[None]]
BinarySender = Callable[[int, int, bytes], Awaitable[None]]
SUPPORTED_REALTIME_PROVIDERS = frozenset({"openai"})


@dataclass(slots=True)
class BridgeBinding:
    bridge: SessionBridge
    _record_ref: dict[str, SessionRecord | None] = field(
        default_factory=lambda: {"record": None}
    )

    def bind_record(self, record: SessionRecord) -> None:
        self._record_ref["record"] = record


def build_debug_mock_capture_bridge(
    *,
    settings: Settings,
    session_id: str,
) -> BridgeBinding:
    from backend.debug.mock_capture import IOSMockCaptureBridge

    record_ref: dict[str, SessionRecord | None] = {"record": None}
    bridge = IOSMockCaptureBridge(
        session_id=session_id,
        dump_input_audio_enabled=settings.backend_debug_dump_input_audio,
        dump_input_audio_dir=str(settings.backend_debug_dump_input_audio_dir),
    )
    return BridgeBinding(bridge=bridge, _record_ref=record_ref)


@dataclass(frozen=True, slots=True)
class RealtimeProviderFactory:
    settings: Settings

    def __post_init__(self) -> None:
        if self.settings.realtime_provider not in SUPPORTED_REALTIME_PROVIDERS:
            supported = ", ".join(sorted(SUPPORTED_REALTIME_PROVIDERS))
            raise ValueError(
                f"Unsupported REALTIME_PROVIDER={self.settings.realtime_provider!r}. "
                f"Supported values: {supported}"
            )

    @property
    def provider_name(self) -> str:
        return self.settings.realtime_provider

    def build_session_bridge(
        self,
        *,
        session_id: str,
        send_control: ControlSender,
        send_server_audio: BinarySender,
        realtime_tooling_runtime: RealtimeToolingRuntime | None = None,
    ) -> BridgeBinding:
        if self.provider_name == "openai":
            from backend.realtime.providers.openai import build_openai_session_bridge

            return build_openai_session_bridge(
                settings=self.settings,
                session_id=session_id,
                send_control=send_control,
                send_server_audio=send_server_audio,
                realtime_tooling_runtime=realtime_tooling_runtime,
            )
        raise RuntimeError(f"Unsupported realtime provider: {self.provider_name}")
