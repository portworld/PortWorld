from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from backend.bridge import IOSRealtimeBridge
from backend.config import settings
from backend.contracts import IOSEnvelope, make_envelope
from backend.frame_codec import (
    CLIENT_AUDIO_FRAME_TYPE,
    decode_frame,
    encode_frame,
)
from backend.openai_realtime_client import OpenAIRealtimeClient, RealtimeClientError
from backend.session_registry import SessionRecord, session_registry

router = APIRouter()
logger = logging.getLogger(__name__)

EXPECTED_CLIENT_AUDIO_ENCODING = "pcm_s16le"
EXPECTED_CLIENT_AUDIO_CHANNELS = 1
EXPECTED_CLIENT_AUDIO_SAMPLE_RATE = 24_000


@router.websocket("/ws/session")
async def ws_session(websocket: WebSocket) -> None:
    await websocket.accept()

    active_session: SessionRecord | None = None

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
        await websocket.send_json(envelope.model_dump())

    async def send_server_audio(frame_type: int, ts_ms: int, payload_bytes: bytes) -> None:
        encoded = encode_frame(frame_type, ts_ms, payload_bytes)
        await websocket.send_bytes(encoded)

    try:
        while True:
            message = await websocket.receive()
            message_type = message.get("type")

            if message_type == "websocket.disconnect":
                break
            if message_type != "websocket.receive":
                continue

            raw_bytes = message.get("bytes")
            if raw_bytes is not None:
                if active_session is None:
                    logger.info("Ignoring binary frame before session.activate")
                    continue

                try:
                    frame_type, _, payload_bytes = decode_frame(raw_bytes)
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
                    continue

                if frame_type != CLIENT_AUDIO_FRAME_TYPE:
                    logger.info(
                        "Ignoring unsupported client frame type=%s session=%s",
                        frame_type,
                        active_session.session_id,
                    )
                    continue

                try:
                    await active_session.bridge.append_client_audio(payload_bytes)
                except RealtimeClientError as exc:
                    logger.warning(
                        "Failed forwarding client audio session=%s: %s",
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
                continue

            raw_text = message.get("text")
            if raw_text is None:
                continue

            try:
                envelope = IOSEnvelope.model_validate_json(raw_text)
            except ValidationError:
                await send_control(
                    "error",
                    {
                        "code": "INVALID_CONTROL_ENVELOPE",
                        "message": "Invalid control envelope",
                        "retriable": False,
                    },
                )
                continue
            if envelope.type == "session.activate":
                if active_session is not None:
                    await _deactivate_session(
                        active_session=active_session,
                        send_control=send_control,
                    )
                    active_session = None

                format_error = _validate_client_audio_format_payload(envelope.payload)
                if format_error is not None:
                    await send_control(
                        "error",
                        format_error,
                        fallback_session_id=envelope.session_id,
                    )
                    continue

                try:
                    api_key = settings.require_openai_api_key()
                except RuntimeError:
                    await send_control(
                        "error",
                        {
                            "code": "MISSING_OPENAI_API_KEY",
                            "message": "Server missing OPENAI_API_KEY",
                            "retriable": False,
                        },
                        fallback_session_id=envelope.session_id,
                    )
                    continue

                client = OpenAIRealtimeClient(
                    api_key=api_key,
                    model=settings.openai_realtime_model,
                    voice=settings.openai_realtime_voice,
                    instructions=settings.openai_realtime_instructions,
                    include_turn_detection=settings.openai_realtime_include_turn_detection,
                )
                record_ref: dict[str, SessionRecord | None] = {"record": None}

                bridge = IOSRealtimeBridge(
                    session_id=envelope.session_id,
                    upstream_client=client,
                    send_envelope=lambda m_type, payload: send_control(
                        m_type,
                        payload,
                        target=record_ref["record"],
                        fallback_session_id=envelope.session_id,
                    ),
                    send_binary_frame=send_server_audio,
                    manual_turn_fallback_enabled=settings.openai_realtime_enable_manual_turn_fallback,
                    manual_turn_fallback_delay_ms=settings.openai_realtime_manual_turn_fallback_delay_ms,
                )
                record = await session_registry.register(
                    session_id=envelope.session_id,
                    websocket=websocket,
                    bridge=bridge,
                )
                record_ref["record"] = record
                active_session = record

                try:
                    await bridge.connect_and_start()
                except RealtimeClientError as exc:
                    logger.warning(
                        "Failed to activate session=%s: %s",
                        envelope.session_id,
                        exc,
                    )
                    await send_control(
                        "error",
                        {
                            "code": "UPSTREAM_CONNECT_FAILED",
                            "message": "Failed to connect upstream realtime session",
                            "retriable": True,
                        },
                    )
                    await bridge.close()
                    await session_registry.unregister(
                        envelope.session_id,
                        websocket=websocket,
                    )
                    active_session = None
                    continue

                await send_control(
                    "session.state",
                    {"state": "active"},
                    target=record,
                )
                continue

            if envelope.type == "session.deactivate":
                if active_session is None:
                    continue
                await _deactivate_session(
                    active_session=active_session,
                    send_control=send_control,
                )
                await session_registry.unregister(
                    active_session.session_id,
                    websocket=websocket,
                )
                active_session = None
                continue

            if envelope.type == "health.ping":
                await send_control(
                    "health.pong",
                    {},
                    fallback_session_id=envelope.session_id,
                )
                continue

            logger.info(
                "Ignoring unsupported control type=%s session=%s",
                envelope.type,
                envelope.session_id,
            )

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    finally:
        if active_session is not None:
            await active_session.bridge.close()
            await session_registry.unregister(
                active_session.session_id,
                websocket=websocket,
            )


def _validate_client_audio_format_payload(
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    raw_format = payload.get("client_audio_format")
    if raw_format is None:
        raw_format = payload.get("audio_format")
    if raw_format is None:
        return None

    if not isinstance(raw_format, dict):
        return {
            "code": "INVALID_CLIENT_AUDIO_FORMAT",
            "message": "client_audio_format must be an object",
            "retriable": False,
        }

    encoding = raw_format.get("encoding")
    channels = _as_integral_int(raw_format.get("channels"))
    sample_rate = _as_integral_int(raw_format.get("sample_rate"))

    if (
        not isinstance(encoding, str)
        or channels is None
        or sample_rate is None
    ):
        return {
            "code": "INVALID_CLIENT_AUDIO_FORMAT",
            "message": (
                "client_audio_format requires encoding (string), channels (int), "
                "and sample_rate (int)"
            ),
            "retriable": False,
        }

    normalized_encoding = encoding.strip().lower()
    if (
        normalized_encoding != EXPECTED_CLIENT_AUDIO_ENCODING
        or channels != EXPECTED_CLIENT_AUDIO_CHANNELS
        or sample_rate != EXPECTED_CLIENT_AUDIO_SAMPLE_RATE
    ):
        return {
            "code": "UNSUPPORTED_CLIENT_AUDIO_FORMAT",
            "message": (
                "Unsupported client audio format. Expected "
                f"{EXPECTED_CLIENT_AUDIO_ENCODING}/{EXPECTED_CLIENT_AUDIO_CHANNELS}ch/"
                f"{EXPECTED_CLIENT_AUDIO_SAMPLE_RATE}Hz."
            ),
            "retriable": False,
        }

    return None


def _as_integral_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


async def _deactivate_session(
    *,
    active_session: SessionRecord,
    send_control: Callable[..., Awaitable[None]],
) -> None:
    await send_control(
        "session.state",
        {"state": "ended"},
        target=active_session,
    )
    await active_session.bridge.close()
