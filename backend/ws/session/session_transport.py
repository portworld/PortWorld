from __future__ import annotations

import logging
from typing import Any

from backend.ws.protocol.contracts import make_envelope
from backend.ws.protocol.frame_codec import encode_frame
from backend.ws.session.session_context import SessionConnectionContext
from backend.ws.session.session_registry import SessionRecord
from backend.ws.session.transport_contracts import SendBinary, SendControl

logger = logging.getLogger(__name__)


def make_send_control(context: SessionConnectionContext) -> SendControl:
    def _payload_keys(payload: dict[str, Any]) -> str:
        return ",".join(sorted(payload.keys()))

    async def send_control(
        message_type: str,
        payload: dict[str, Any],
        *,
        target: SessionRecord | None = None,
        fallback_session_id: str = "unknown",
    ) -> None:
        session = target or context.active_session
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
        if message_type == "error":
            logger.warning(
                "WS_SEND_CONTROL connection_id=%s session=%s type=%s payload_keys=%s",
                context.connection_id,
                envelope.session_id,
                message_type,
                _payload_keys(payload),
            )
        elif message_type == "assistant.playback.control":
            logger.debug(
                "WS_SEND_CONTROL connection_id=%s session=%s type=%s payload_keys=%s",
                context.connection_id,
                envelope.session_id,
                message_type,
                _payload_keys(payload),
            )
        try:
            await context.websocket.send_json(envelope.model_dump())
        except Exception:
            logger.exception(
                "WS_SEND_CONTROL_FAILED connection_id=%s session=%s type=%s",
                context.connection_id,
                envelope.session_id,
                message_type,
            )
            raise

    return send_control


def make_send_server_audio(context: SessionConnectionContext) -> SendBinary:
    async def send_server_audio(frame_type: int, ts_ms: int, payload_bytes: bytes) -> None:
        encoded = encode_frame(frame_type, ts_ms, payload_bytes)
        context.server_audio_frame_count += 1
        context.server_audio_total_bytes += len(payload_bytes)
        if context.server_audio_frame_count == 1 or context.server_audio_frame_count % 50 == 0:
            session_id = (
                context.active_session.session_id
                if context.active_session is not None
                else "unknown"
            )
            logger.debug(
                "WS_SEND_SERVER_AUDIO connection_id=%s session=%s frame=%s payload_bytes=%s total_bytes=%s ts_ms=%s",
                context.connection_id,
                session_id,
                context.server_audio_frame_count,
                len(payload_bytes),
                context.server_audio_total_bytes,
                ts_ms,
            )
        try:
            await context.websocket.send_bytes(encoded)
        except Exception:
            session_id = (
                context.active_session.session_id
                if context.active_session is not None
                else "unknown"
            )
            logger.exception(
                "WS_SEND_SERVER_AUDIO_FAILED connection_id=%s session=%s frame=%s",
                context.connection_id,
                session_id,
                context.server_audio_frame_count,
            )
            raise

    return send_server_audio
