from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from backend.core.settings import Settings
from backend.tools.runtime import RealtimeToolingRuntime
from backend.ws.session_registry import SessionBridge, SessionRecord

ControlSender = Callable[..., Awaitable[None]]
BinarySender = Callable[[int, int, bytes], Awaitable[None]]


class RealtimeProviderBuilder(Protocol):
    def __call__(
        self,
        *,
        settings: Settings,
        session_id: str,
        send_control: ControlSender,
        send_server_audio: BinarySender,
        realtime_tooling_runtime: RealtimeToolingRuntime | None = None,
    ) -> "BridgeBinding": ...


class RealtimeSettingsValidator(Protocol):
    def __call__(self, settings: Settings) -> None: ...


@dataclass(frozen=True, slots=True)
class RealtimeProviderDefinition:
    name: str
    build_bridge: RealtimeProviderBuilder
    validate_settings: RealtimeSettingsValidator
    validate_on_startup: bool = True


class RealtimeProviderRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, RealtimeProviderDefinition] = {}

    def register(self, definition: RealtimeProviderDefinition) -> None:
        if definition.name in self._definitions:
            raise ValueError(f"Realtime provider already registered: {definition.name}")
        self._definitions[definition.name] = definition

    def resolve(self, name: str) -> RealtimeProviderDefinition:
        try:
            return self._definitions[name]
        except KeyError as exc:
            supported = ", ".join(sorted(self._definitions))
            raise ValueError(
                f"Unsupported REALTIME_PROVIDER={name!r}. Supported values: {supported}"
            ) from exc


def build_default_realtime_provider_registry() -> RealtimeProviderRegistry:
    from backend.realtime.providers.openai import (
        build_openai_session_bridge,
        validate_openai_realtime_settings,
    )

    registry = RealtimeProviderRegistry()
    registry.register(
        RealtimeProviderDefinition(
            name="openai",
            build_bridge=build_openai_session_bridge,
            validate_settings=validate_openai_realtime_settings,
            validate_on_startup=False,
        )
    )
    return registry


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
    registry: RealtimeProviderRegistry = field(
        default_factory=build_default_realtime_provider_registry
    )
    _definition: RealtimeProviderDefinition = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_definition",
            self.registry.resolve(self.settings.realtime_provider),
        )

    @property
    def provider_name(self) -> str:
        return self._definition.name

    def validate_configuration(self) -> None:
        self._definition.validate_settings(self.settings)

    def validate_startup_configuration(self) -> None:
        if not self._definition.validate_on_startup:
            return
        self.validate_configuration()

    def build_session_bridge(
        self,
        *,
        session_id: str,
        send_control: ControlSender,
        send_server_audio: BinarySender,
        realtime_tooling_runtime: RealtimeToolingRuntime | None = None,
    ) -> BridgeBinding:
        return self._definition.build_bridge(
            settings=self.settings,
            session_id=session_id,
            send_control=send_control,
            send_server_audio=send_server_audio,
            realtime_tooling_runtime=realtime_tooling_runtime,
        )
