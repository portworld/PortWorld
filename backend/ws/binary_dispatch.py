from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from backend.core.settings import Settings
from backend.realtime.client import RealtimeClientError
from backend.ws.frame_codec import (
    CLIENT_AUDIO_FRAME_TYPE,
    CLIENT_PROBE_FRAME_TYPE,
    decode_frame,
)
from backend.ws.session_registry import SessionRecord
from backend.ws.telemetry import SessionTelemetry

logger = logging.getLogger(__name__)

SendControl = Callable[..., Awaitable[None]]


async def dispatch_binary_frame(
    *,
    raw_bytes: bytes,
    active_session: SessionRecord | None,
    send_control: SendControl,
    telemetry: SessionTelemetry,
    connection_id: int,
    settings: Settings,
) -> bool:
    if active_session is None:
        logger.info("Ignoring binary frame before session.activate")
        return True

    try:
        frame_type, frame_ts_ms, payload_bytes = decode_frame(raw_bytes)
    except Exception as exc:
        logger.warning(
            "Invalid binary frame for session=%s: %s",
            active_session.session_id,
            exc,
        )
        await send_control(
            "error",
            {
                "code": "INVALID_BINARY_FRAME",
                "message": "Invalid binary frame",
                "retriable": False,
            },
        )
        return True

    if frame_type != CLIENT_AUDIO_FRAME_TYPE:
        if frame_type == CLIENT_PROBE_FRAME_TYPE:
            if not settings.backend_enable_devtools_protocol:
                logger.info(
                    "Ignoring disabled probe frame connection_id=%s session=%s",
                    connection_id,
                    active_session.session_id,
                )
                await send_control(
                    "error",
                    {
                        "code": "DEVTOOLS_PROTOCOL_DISABLED",
                        "message": "Probe frames are disabled on this server",
                        "retriable": False,
                    },
                )
                return True
            await send_control(
                "transport.uplink.ack",
                telemetry.record_probe_frame(
                    active_session=active_session,
                    payload_bytes=payload_bytes,
                    frame_ts_ms=frame_ts_ms,
                ),
            )
            return True
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
        await send_control(
            "error",
            {
                "code": "UPSTREAM_SEND_FAILED",
                "message": "Failed to forward audio upstream",
                "retriable": True,
            },
        )
    return True
