from __future__ import annotations

import base64
import binascii
import itertools
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
    CLIENT_PROBE_FRAME_TYPE,
    decode_frame,
    encode_frame,
)
from backend.openai_realtime_client import OpenAIRealtimeClient, RealtimeClientError
from backend.session_registry import SessionRecord, session_registry

router = APIRouter()
logger = logging.getLogger(__name__)
_connection_ids = itertools.count(1)

EXPECTED_CLIENT_AUDIO_ENCODING = "pcm_s16le"
EXPECTED_CLIENT_AUDIO_CHANNELS = 1
EXPECTED_CLIENT_AUDIO_SAMPLE_RATE = 24_000
MAX_TRACE_TEXT_PREVIEW = 120


@router.websocket("/ws/session")
async def ws_session(websocket: WebSocket) -> None:
    await websocket.accept()
    connection_id = next(_connection_ids)

    active_session: SessionRecord | None = None
    did_log_first_client_audio_frame = False
    did_warn_text_audio_fallback_deprecated = False
    did_emit_uplink_ack = False
    uplink_ack_count = 0
    client_audio_frame_count = 0
    client_audio_total_bytes = 0
    uplink_ack_every_n_frames = settings.openai_realtime_uplink_ack_every_n_frames

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
            _trace_ws_message(
                message,
                active_session=active_session,
                connection_id=connection_id,
            )

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
                    continue

                if frame_type != CLIENT_AUDIO_FRAME_TYPE:
                    if frame_type == CLIENT_PROBE_FRAME_TYPE:
                        logger.warning(
                            "Client probe frame received connection_id=%s session=%s bytes=%s ts_ms=%s",
                            connection_id,
                            active_session.session_id,
                            len(payload_bytes),
                            frame_ts_ms,
                        )
                        await send_control(
                            "transport.uplink.ack",
                            {
                                "frames_received": client_audio_frame_count,
                                "bytes_received": client_audio_total_bytes,
                                "probe_acknowledged": True,
                            },
                        )
                        did_emit_uplink_ack = True
                        uplink_ack_count += 1
                        continue
                    logger.info(
                        "Ignoring unsupported client frame type=%s connection_id=%s session=%s",
                        frame_type,
                        connection_id,
                        active_session.session_id,
                    )
                    continue

                client_audio_frame_count += 1
                client_audio_total_bytes += len(payload_bytes)
                if not did_log_first_client_audio_frame:
                    did_log_first_client_audio_frame = True
                    logger.warning(
                        "First client audio frame received connection_id=%s session=%s bytes=%s total_bytes=%s ts_ms=%s",
                        connection_id,
                        active_session.session_id,
                        len(payload_bytes),
                        client_audio_total_bytes,
                        frame_ts_ms,
                    )
                elif client_audio_frame_count <= 10:
                    logger.warning(
                        "Client audio frame received connection_id=%s session=%s frame=%s bytes=%s total_bytes=%s ts_ms=%s",
                        connection_id,
                        active_session.session_id,
                        client_audio_frame_count,
                        len(payload_bytes),
                        client_audio_total_bytes,
                        frame_ts_ms,
                    )
                elif client_audio_frame_count % uplink_ack_every_n_frames == 0:
                    logger.warning(
                        "Client audio frame count connection_id=%s session=%s frames=%s total_bytes=%s ts_ms=%s",
                        connection_id,
                        active_session.session_id,
                        client_audio_frame_count,
                        client_audio_total_bytes,
                        frame_ts_ms,
                    )

                if (
                    client_audio_frame_count == 1
                    or client_audio_frame_count % uplink_ack_every_n_frames == 0
                ):
                    await send_control(
                        "transport.uplink.ack",
                        {
                            "frames_received": client_audio_frame_count,
                            "bytes_received": client_audio_total_bytes,
                            "probe_acknowledged": False,
                        },
                    )
                    did_emit_uplink_ack = True
                    uplink_ack_count += 1

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
                continue

            raw_text = message.get("text")
            if raw_text is None:
                continue

            try:
                envelope = IOSEnvelope.model_validate_json(raw_text)
            except ValidationError:
                logger.warning(
                    "Invalid control envelope session=%s preview=%s",
                    active_session.session_id if active_session is not None else "unknown",
                    _sanitize_text_preview(raw_text),
                )
                await send_control(
                    "error",
                    {
                        "code": "INVALID_CONTROL_ENVELOPE",
                        "message": "Invalid control envelope",
                        "retriable": False,
                    },
                )
                continue
            logger.warning(
                "Inbound control type=%s session=%s seq=%s",
                envelope.type,
                envelope.session_id,
                envelope.seq,
            )
            if envelope.type == "session.activate":
                if active_session is not None:
                    await _deactivate_and_unregister_session(
                        active_session=active_session,
                        websocket=websocket,
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
                    dump_input_audio_enabled=settings.openai_debug_dump_input_audio,
                    dump_input_audio_dir=settings.openai_debug_dump_input_audio_dir,
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
                logger.warning("Session activated session=%s", envelope.session_id)
                continue

            if envelope.type == "session.deactivate":
                if active_session is None:
                    continue
                await _deactivate_and_unregister_session(
                    active_session=active_session,
                    websocket=websocket,
                    send_control=send_control,
                )
                active_session = None
                continue

            if envelope.type == "health.ping":
                await send_control(
                    "health.pong",
                    {},
                    fallback_session_id=envelope.session_id,
                )
                logger.warning("Health ping session=%s", envelope.session_id)
                continue

            if envelope.type == "debug.payload_sweep":
                payload = envelope.payload if isinstance(envelope.payload, dict) else {}
                logger.warning(
                    "Debug payload sweep control received connection_id=%s session=%s mode=%s index=%s payload_bytes=%s",
                    connection_id,
                    envelope.session_id,
                    payload.get("mode"),
                    payload.get("index"),
                    payload.get("payload_bytes"),
                )
                continue

            if envelope.type == "client.audio":
                if active_session is None:
                    logger.info("Ignoring client.audio before session.activate")
                    continue
                if not settings.openai_realtime_allow_text_audio_fallback:
                    await send_control(
                        "error",
                        {
                            "code": "TEXT_AUDIO_FALLBACK_DISABLED",
                            "message": "client.audio fallback is disabled on this server",
                            "retriable": False,
                        },
                    )
                    continue
                payload = envelope.payload if isinstance(envelope.payload, dict) else {}
                audio_b64 = payload.get("audio_b64")
                if audio_b64 is None:
                    audio_b64 = payload.get("audio")
                if not isinstance(audio_b64, str):
                    await send_control(
                        "error",
                        {
                            "code": "INVALID_CLIENT_AUDIO",
                            "message": "client.audio requires payload.audio_b64",
                            "retriable": False,
                        },
                    )
                    continue
                try:
                    payload_bytes = base64.b64decode(audio_b64, validate=True)
                except (binascii.Error, ValueError):
                    await send_control(
                        "error",
                        {
                            "code": "INVALID_CLIENT_AUDIO",
                            "message": "client.audio payload is not valid base64",
                            "retriable": False,
                        },
                    )
                    continue

                if not payload_bytes:
                    continue
                if not did_warn_text_audio_fallback_deprecated:
                    did_warn_text_audio_fallback_deprecated = True
                    logger.warning(
                        "Deprecated client.audio text/base64 fallback used connection_id=%s session=%s. "
                        "Use binary websocket audio frames instead.",
                        connection_id,
                        active_session.session_id,
                    )

                client_audio_frame_count += 1
                client_audio_total_bytes += len(payload_bytes)
                if not did_log_first_client_audio_frame:
                    did_log_first_client_audio_frame = True
                    logger.warning(
                        "First client audio frame received connection_id=%s session=%s bytes=%s total_bytes=%s mode=text_base64",
                        connection_id,
                        active_session.session_id,
                        len(payload_bytes),
                        client_audio_total_bytes,
                    )
                elif client_audio_frame_count % uplink_ack_every_n_frames == 0:
                    logger.warning(
                        "Client audio frame count connection_id=%s session=%s frames=%s total_bytes=%s mode=text_base64",
                        connection_id,
                        active_session.session_id,
                        client_audio_frame_count,
                        client_audio_total_bytes,
                    )

                if (
                    client_audio_frame_count == 1
                    or client_audio_frame_count % uplink_ack_every_n_frames == 0
                ):
                    await send_control(
                        "transport.uplink.ack",
                        {
                            "frames_received": client_audio_frame_count,
                            "bytes_received": client_audio_total_bytes,
                            "probe_acknowledged": False,
                        },
                    )
                    did_emit_uplink_ack = True
                    uplink_ack_count += 1

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
                continue

            if envelope.type == "health.stats":
                payload = envelope.payload if isinstance(envelope.payload, dict) else {}
                enqueued = _as_integral_int(payload.get("realtime_audio_frames_enqueued"))
                attempted = _as_integral_int(payload.get("realtime_audio_frames_send_attempted"))
                sent = _as_integral_int(payload.get("realtime_audio_frames_sent"))
                send_failures = _as_integral_int(payload.get("realtime_audio_send_failures"))
                last_send_error = payload.get("realtime_audio_last_send_error")
                logger.warning(
                    "Health stats session=%s ios_enqueued=%s ios_attempted=%s ios_sent=%s ios_send_failures=%s ios_last_send_error=%s backend_frames=%s backend_bytes=%s uplink_ack_emitted=%s uplink_ack_count=%s",
                    envelope.session_id,
                    enqueued,
                    attempted,
                    sent,
                    send_failures,
                    last_send_error if isinstance(last_send_error, str) else "-",
                    client_audio_frame_count,
                    client_audio_total_bytes,
                    did_emit_uplink_ack,
                    uplink_ack_count,
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
            await _deactivate_and_unregister_session(
                active_session=active_session,
                websocket=websocket,
                send_control=send_control,
                emit_session_state=False,
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


async def _deactivate_and_unregister_session(
    *,
    active_session: SessionRecord,
    websocket: WebSocket,
    send_control: Callable[..., Awaitable[None]],
    emit_session_state: bool = True,
) -> None:
    if emit_session_state:
        await _deactivate_session(
            active_session=active_session,
            send_control=send_control,
        )
    else:
        await active_session.bridge.close()
    await session_registry.unregister(
        active_session.session_id,
        websocket=websocket,
    )


def _trace_ws_message(
    message: dict[str, Any],
    *,
    active_session: SessionRecord | None,
    connection_id: int,
) -> None:
    if not settings.openai_debug_trace_ws_messages:
        return

    session_id = active_session.session_id if active_session is not None else "unbound"
    message_type = message.get("type", "unknown")
    raw_text = message.get("text")
    raw_bytes = message.get("bytes")
    if isinstance(raw_text, str):
        logger.warning(
            "WS_TRACE connection_id=%s type=%s session=%s text_len=%s preview=%s",
            connection_id,
            message_type,
            session_id,
            len(raw_text),
            _sanitize_text_preview(raw_text),
        )
        return
    if isinstance(raw_bytes, (bytes, bytearray, memoryview)):
        logger.warning(
            "WS_TRACE connection_id=%s type=%s session=%s byte_len=%s",
            connection_id,
            message_type,
            session_id,
            len(raw_bytes),
        )
        return
    logger.warning(
        "WS_TRACE connection_id=%s type=%s session=%s code=%s reason=%s keys=%s",
        connection_id,
        message_type,
        session_id,
        message.get("code"),
        message.get("reason"),
        sorted(message.keys()),
    )


def _sanitize_text_preview(raw_text: str) -> str:
    collapsed = " ".join(raw_text.split())
    if len(collapsed) <= MAX_TRACE_TEXT_PREVIEW:
        return collapsed
    return f"{collapsed[:MAX_TRACE_TEXT_PREVIEW]}..."
