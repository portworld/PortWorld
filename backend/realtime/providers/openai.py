from __future__ import annotations

from backend.core.settings import Settings
from backend.realtime.bridge import IOSRealtimeBridge
from backend.realtime.client import OpenAIRealtimeClient
from backend.realtime.contracts import RealtimeProviderCapabilities
from backend.realtime.factory import (
    BinarySender,
    BridgeBinding,
    BridgeBindingContext,
    ControlSender,
)
from backend.tools.runtime import RealtimeToolingRuntime


OPENAI_REALTIME_CAPABILITIES = RealtimeProviderCapabilities(
    streaming_audio_input=True,
    streaming_audio_output=True,
    server_vad=True,
    manual_turn_commit_required=False,
    tool_calling=True,
    tool_result_submission_mode="conversation_item",
    voice_selection=True,
    interruption_cancel=True,
    startup_validation=True,
)


def validate_openai_realtime_settings(settings: Settings) -> None:
    settings.require_openai_api_key()


def build_openai_session_bridge(
    *,
    settings: Settings,
    session_id: str,
    send_control: ControlSender,
    send_server_audio: BinarySender,
    realtime_tooling_runtime: RealtimeToolingRuntime | None = None,
    session_instructions: str | None = None,
    auto_start_response: bool = False,
) -> BridgeBinding:
    context = BridgeBindingContext()
    api_key = settings.require_openai_api_key()
    base_instructions = settings.realtime_instructions
    if isinstance(session_instructions, str) and session_instructions.strip():
        base_instructions = session_instructions.strip()
    effective_instructions = base_instructions
    if realtime_tooling_runtime is not None:
        effective_instructions = realtime_tooling_runtime.build_session_instructions(
            base_instructions=base_instructions,
        )
    client = OpenAIRealtimeClient(
        api_key=api_key,
        model=settings.realtime_model,
        voice=settings.realtime_voice,
        instructions=effective_instructions,
        include_turn_detection=settings.realtime_include_turn_detection,
    )
    bridge = IOSRealtimeBridge(
        session_id=session_id,
        upstream_client=client,
        send_envelope=lambda message_type, payload: send_control(
            message_type,
            payload,
            target=context.record,
            fallback_session_id=session_id,
        ),
        send_binary_frame=send_server_audio,
        server_turn_detection_enabled=settings.realtime_include_turn_detection,
        manual_turn_fallback_enabled=settings.realtime_enable_manual_turn_fallback,
        manual_turn_fallback_delay_ms=settings.realtime_manual_turn_fallback_delay_ms,
        tooling_runtime=realtime_tooling_runtime,
        session_instructions=effective_instructions,
        auto_start_response=auto_start_response,
    )
    return BridgeBinding(bridge=bridge, context=context)
