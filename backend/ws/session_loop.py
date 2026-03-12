from __future__ import annotations

import logging

from backend.ws.binary_dispatch import dispatch_binary_frame
from backend.ws.control_dispatch import dispatch_control_envelope, parse_control_envelope
from backend.ws.session_context import SessionConnectionContext
from backend.ws.session_runtime import trace_ws_message
from backend.ws.session_transport import SendBinary, SendControl

logger = logging.getLogger(__name__)


async def process_next_websocket_message(
    *,
    context: SessionConnectionContext,
    send_control: SendControl,
    send_server_audio: SendBinary,
) -> bool:
    message = await context.websocket.receive()
    message_type = message.get("type")
    context.telemetry.log_receive_shape(message)
    trace_ws_message(
        message,
        active_session=context.active_session,
        connection_id=context.connection_id,
        trace_ws_messages_enabled=context.runtime.settings.backend_debug_trace_ws_messages,
    )

    if message_type == "websocket.disconnect":
        return False
    if message_type != "websocket.receive":
        return True

    raw_bytes = message.get("bytes")
    if raw_bytes is not None:
        handled = await dispatch_binary_frame(
            raw_bytes=raw_bytes,
            active_session=context.active_session,
            send_control=send_control,
            telemetry=context.telemetry,
            connection_id=context.connection_id,
            settings=context.runtime.settings,
        )
        if handled:
            return True

    raw_text = message.get("text")
    if raw_text is None:
        return True

    envelope = await parse_control_envelope(
        raw_text=raw_text,
        active_session=context.active_session,
        send_control=send_control,
    )
    if envelope is None:
        return True

    allowed = await _allow_session_activation(
        context=context,
        envelope_type=envelope.type,
        envelope_session_id=envelope.session_id,
        send_control=send_control,
    )
    if not allowed:
        return True

    dispatch_result = await dispatch_control_envelope(
        envelope=envelope,
        active_session=context.active_session,
        websocket=context.websocket,
        send_control=send_control,
        send_server_audio=send_server_audio,
        telemetry=context.telemetry,
        settings=context.runtime.settings,
        build_session_bridge=context.runtime.make_session_bridge,
        storage=context.runtime.storage,
        vision_memory_runtime=context.runtime.vision_memory_runtime,
    )
    context.active_session = dispatch_result.active_session
    if not dispatch_result.handled:
        logger.info(
            "Ignoring unsupported control type=%s session=%s",
            envelope.type,
            envelope.session_id,
        )
    return True


async def _allow_session_activation(
    *,
    context: SessionConnectionContext,
    envelope_type: str,
    envelope_session_id: str,
    send_control: SendControl,
) -> bool:
    if envelope_type != "session.activate":
        return True

    activation_rate_decision = await context.runtime.limit_ws_session_activation(
        client_ip=context.client_ip,
        session_id=envelope_session_id,
    )
    if activation_rate_decision.allowed:
        return True

    logger.warning(
        "Rate-limited session.activate ip=%s session=%s scope=%s retry_after_seconds=%s",
        context.client_ip,
        envelope_session_id,
        activation_rate_decision.scope,
        activation_rate_decision.retry_after_seconds,
    )
    await send_control(
        "error",
        {
            "code": "RATE_LIMITED",
            "message": (
                "Session activation rate limit exceeded "
                f"for {activation_rate_decision.scope}"
            ),
            "retriable": True,
            "retry_after_seconds": activation_rate_decision.retry_after_seconds,
        },
        fallback_session_id=envelope_session_id,
    )
    return False
