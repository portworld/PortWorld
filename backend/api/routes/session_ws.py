from __future__ import annotations

import itertools
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.core.auth import reject_ws_if_unauthorized
from backend.core.runtime import get_app_runtime
from backend.ws.binary_dispatch import dispatch_binary_frame
from backend.ws.control_dispatch import dispatch_control_envelope, parse_control_envelope
from backend.ws.contracts import make_envelope
from backend.ws.frame_codec import encode_frame
from backend.ws.session_registry import SessionRecord
from backend.ws.session_runtime import deactivate_and_unregister_session, trace_ws_message
from backend.ws.telemetry import SessionTelemetry

router = APIRouter()
logger = logging.getLogger(__name__)
_connection_ids = itertools.count(1)


def _client_ip_from_websocket(websocket: WebSocket) -> str:
    client = websocket.client
    if client is None:
        return "unknown"
    host = (client.host or "").strip()
    return host or "unknown"


@router.websocket("/ws/session")
async def ws_session(websocket: WebSocket) -> None:
    runtime = get_app_runtime(websocket.app)
    client_ip = _client_ip_from_websocket(websocket)
    connect_rate_decision = await runtime.limit_ws_connect(client_ip=client_ip)
    if not connect_rate_decision.allowed:
        logger.warning(
            "Rejected rate-limited websocket connect ip=%s retry_after_seconds=%s",
            client_ip,
            connect_rate_decision.retry_after_seconds,
        )
        await websocket.close(code=1013, reason="Rate limited")
        return
    if await reject_ws_if_unauthorized(websocket=websocket, settings=runtime.settings):
        logger.warning("Rejected unauthorized websocket session request")
        return
    await websocket.accept()
    connection_id = next(_connection_ids)

    active_session: SessionRecord | None = None
    server_audio_frame_count = 0
    server_audio_total_bytes = 0
    telemetry = SessionTelemetry(
        connection_id=connection_id,
        uplink_ack_every_n_frames=runtime.settings.backend_uplink_ack_every_n_frames,
    )

    async def send_control(
        message_type: str,
        payload: dict[str, Any],
        *,
        target: SessionRecord | None = None,
        fallback_session_id: str = "unknown",
    ) -> None:
        session = target or active_session
        if session is None:
            envelope = make_envelope(
                message_type=message_type,
                session_id=fallback_session_id,
                seq=0,
                payload=payload,
            )
        else:
            envelope = make_envelope(
                message_type=message_type,
                session_id=session.session_id,
                seq=session.next_seq(),
                payload=payload,
            )
        if message_type in {"assistant.playback.control", "error"}:
            logger.warning(
                "WS_SEND_CONTROL connection_id=%s session=%s type=%s payload=%s",
                connection_id,
                envelope.session_id,
                message_type,
                payload,
            )
        try:
            await websocket.send_json(envelope.model_dump())
        except Exception:
            logger.exception(
                "WS_SEND_CONTROL_FAILED connection_id=%s session=%s type=%s",
                connection_id,
                envelope.session_id,
                message_type,
            )
            raise

    async def send_server_audio(frame_type: int, ts_ms: int, payload_bytes: bytes) -> None:
        nonlocal server_audio_frame_count
        nonlocal server_audio_total_bytes
        encoded = encode_frame(frame_type, ts_ms, payload_bytes)
        server_audio_frame_count += 1
        server_audio_total_bytes += len(payload_bytes)
        if server_audio_frame_count == 1 or server_audio_frame_count % 50 == 0:
            session_id = active_session.session_id if active_session is not None else "unknown"
            logger.warning(
                "WS_SEND_SERVER_AUDIO connection_id=%s session=%s frame=%s payload_bytes=%s total_bytes=%s ts_ms=%s",
                connection_id,
                session_id,
                server_audio_frame_count,
                len(payload_bytes),
                server_audio_total_bytes,
                ts_ms,
            )
        try:
            await websocket.send_bytes(encoded)
        except Exception:
            session_id = active_session.session_id if active_session is not None else "unknown"
            logger.exception(
                "WS_SEND_SERVER_AUDIO_FAILED connection_id=%s session=%s frame=%s",
                connection_id,
                session_id,
                server_audio_frame_count,
            )
            raise

    try:
        while True:
            message = await websocket.receive()
            message_type = message.get("type")
            telemetry.log_receive_shape(message)
            trace_ws_message(
                message,
                active_session=active_session,
                connection_id=connection_id,
                trace_ws_messages_enabled=runtime.settings.backend_debug_trace_ws_messages,
            )

            if message_type == "websocket.disconnect":
                break
            if message_type != "websocket.receive":
                continue

            raw_bytes = message.get("bytes")
            if raw_bytes is not None:
                handled = await dispatch_binary_frame(
                    raw_bytes=raw_bytes,
                    active_session=active_session,
                    send_control=send_control,
                    telemetry=telemetry,
                    connection_id=connection_id,
                    settings=runtime.settings,
                )
                if handled:
                    continue

            raw_text = message.get("text")
            if raw_text is None:
                continue

            envelope = await parse_control_envelope(
                raw_text=raw_text,
                active_session=active_session,
                send_control=send_control,
            )
            if envelope is None:
                continue

            if envelope.type == "session.activate":
                activation_rate_decision = await runtime.limit_ws_session_activation(
                    client_ip=client_ip,
                    session_id=envelope.session_id,
                )
                if not activation_rate_decision.allowed:
                    logger.warning(
                        "Rate-limited session.activate ip=%s session=%s scope=%s retry_after_seconds=%s",
                        client_ip,
                        envelope.session_id,
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
                        fallback_session_id=envelope.session_id,
                    )
                    continue

            dispatch_result = await dispatch_control_envelope(
                envelope=envelope,
                active_session=active_session,
                websocket=websocket,
                send_control=send_control,
                send_server_audio=send_server_audio,
                telemetry=telemetry,
                settings=runtime.settings,
                build_session_bridge=runtime.make_session_bridge,
                storage=runtime.storage,
                vision_memory_runtime=runtime.vision_memory_runtime,
            )
            active_session = dispatch_result.active_session
            if not dispatch_result.handled:
                logger.info(
                    "Ignoring unsupported control type=%s session=%s",
                    envelope.type,
                    envelope.session_id,
                )

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    finally:
        if active_session is not None:
            await deactivate_and_unregister_session(
                active_session=active_session,
                websocket=websocket,
                send_control=send_control,
                storage=runtime.storage,
                session_memory_retention_days=runtime.settings.backend_session_memory_retention_days,
                vision_memory_runtime=runtime.vision_memory_runtime,
                emit_session_state=False,
            )
