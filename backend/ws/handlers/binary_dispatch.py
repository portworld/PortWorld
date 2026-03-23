from __future__ import annotations

import logging

from backend.realtime.client import RealtimeClientError
from backend.ws.protocol.error_utils import send_error
from backend.ws.protocol.frame_codec import (
    CLIENT_AUDIO_FRAME_TYPE,
    FrameCodecError,
    decode_frame,
)
from backend.ws.session.session_registry import SessionRecord
from backend.ws.session.transport_contracts import SendControl
from backend.ws.telemetry import SessionTelemetry

logger = logging.getLogger(__name__)


async def dispatch_binary_frame(
    *,
    raw_bytes: bytes,
    active_session: SessionRecord | None,
    send_control: SendControl,
    telemetry: SessionTelemetry,
    connection_id: int,
) -> bool:
    if active_session is None:
        logger.info("Ignoring binary frame before session.activate")
        return True

    try:
        frame_type, frame_ts_ms, payload_bytes = decode_frame(raw_bytes)
    except (FrameCodecError, TypeError) as exc:
        logger.warning(
            "Invalid binary frame for session=%s: %s",
            active_session.session_id,
            exc,
        )
        await send_error(
            send_control,
            code="INVALID_BINARY_FRAME",
            message="Invalid binary frame",
            retriable=False,
        )
        return True

    if frame_type != CLIENT_AUDIO_FRAME_TYPE:
        logger.info(
            "Ignoring unsupported client frame type=%s connection_id=%s session=%s",
            frame_type,
            connection_id,
            active_session.session_id,
        )
        return True

    if not payload_bytes:
        await send_control(
            "error",
            telemetry.record_empty_binary_frame(
                active_session=active_session,
                frame_ts_ms=frame_ts_ms,
            ),
        )
        return True

    ack_payload = telemetry.record_binary_audio_frame(
        active_session=active_session,
        payload_bytes=payload_bytes,
        frame_ts_ms=frame_ts_ms,
    )
    if ack_payload is not None:
        await send_control("transport.uplink.ack", ack_payload)

    try:
        await active_session.bridge.append_client_audio(payload_bytes)
    except RealtimeClientError as exc:
        logger.warning(
            "Failed forwarding client audio connection_id=%s session=%s: %s",
            connection_id,
            active_session.session_id,
            exc,
        )
        await send_error(
            send_control,
            code="UPSTREAM_SEND_FAILED",
            message="Failed to forward audio upstream",
            retriable=True,
        )
    return True
