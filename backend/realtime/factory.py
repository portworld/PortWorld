from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from backend.core.settings import Settings
from backend.debug.mock_capture import IOSMockCaptureBridge
from backend.realtime.bridge import IOSRealtimeBridge
from backend.realtime.client import OpenAIRealtimeClient
from backend.ws.session_registry import SessionRecord

ControlSender = Callable[..., Awaitable[None]]
BinarySender = Callable[[int, int, bytes], Awaitable[None]]


@dataclass(slots=True)
class BridgeBinding:
    bridge: IOSRealtimeBridge | IOSMockCaptureBridge
    _record_ref: dict[str, SessionRecord | None] = field(
        default_factory=lambda: {"record": None}
    )

    def bind_record(self, record: SessionRecord) -> None:
        self._record_ref["record"] = record


def build_session_bridge(
    *,
    settings: Settings,
    session_id: str,
    send_control: ControlSender,
    send_server_audio: BinarySender,
) -> BridgeBinding:
    record_ref: dict[str, SessionRecord | None] = {"record": None}
    if settings.openai_debug_mock_capture_mode:
        bridge = IOSMockCaptureBridge(
            session_id=session_id,
            dump_input_audio_enabled=settings.openai_debug_dump_input_audio,
            dump_input_audio_dir=settings.openai_debug_dump_input_audio_dir,
        )
        return BridgeBinding(bridge=bridge, _record_ref=record_ref)

    api_key = settings.require_openai_api_key()
    client = OpenAIRealtimeClient(
        api_key=api_key,
        model=settings.openai_realtime_model,
        voice=settings.openai_realtime_voice,
        instructions=settings.openai_realtime_instructions,
        include_turn_detection=settings.openai_realtime_include_turn_detection,
        trace_events=settings.openai_debug_trace_ws_messages,
    )
    bridge = IOSRealtimeBridge(
        session_id=session_id,
        upstream_client=client,
        send_envelope=lambda message_type, payload: send_control(
            message_type,
            payload,
            target=record_ref["record"],
            fallback_session_id=session_id,
        ),
        send_binary_frame=send_server_audio,
        server_turn_detection_enabled=settings.openai_realtime_include_turn_detection,
        manual_turn_fallback_enabled=settings.openai_realtime_enable_manual_turn_fallback,
        manual_turn_fallback_delay_ms=settings.openai_realtime_manual_turn_fallback_delay_ms,
        dump_input_audio_enabled=settings.openai_debug_dump_input_audio,
        dump_input_audio_dir=settings.openai_debug_dump_input_audio_dir,
    )
    return BridgeBinding(bridge=bridge, _record_ref=record_ref)
