from __future__ import annotations

import base64
import binascii
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import WebSocket
from pydantic import ValidationError

from backend.core.settings import Settings
from backend.core.storage import BackendStorage
from backend.realtime.client import RealtimeClientError
from backend.realtime.factory import BridgeBinding
from backend.vision.runtime import VisionMemoryRuntime
from backend.ws.contracts import IOSEnvelope
from backend.ws.session_activation import activate_session
from backend.ws.session_registry import SessionRecord
from backend.ws.session_runtime import (
    as_integral_int,
    deactivate_and_unregister_session,
    sanitize_text_preview,
)
from backend.ws.telemetry import SessionTelemetry

logger = logging.getLogger(__name__)

SendControl = Callable[..., Awaitable[None]]
SendBinary = Callable[[int, int, bytes], Awaitable[None]]


@dataclass(slots=True)
class ControlDispatchResult:
    active_session: SessionRecord | None
    handled: bool


async def parse_control_envelope(
    *,
    raw_text: str,
    active_session: SessionRecord | None,
    send_control: SendControl,
) -> IOSEnvelope | None:
    try:
        return IOSEnvelope.model_validate_json(raw_text)
    except ValidationError:
        logger.warning(
            "Invalid control envelope session=%s preview=%s",
            active_session.session_id if active_session is not None else "unknown",
            sanitize_text_preview(raw_text),
        )
        await send_control(
            "error",
            {
                "code": "INVALID_CONTROL_ENVELOPE",
                "message": "Invalid control envelope",
                "retriable": False,
            },
        )
        return None


async def dispatch_control_envelope(
    *,
    envelope: IOSEnvelope,
    active_session: SessionRecord | None,
    websocket: WebSocket,
    send_control: SendControl,
    send_server_audio: SendBinary,
    telemetry: SessionTelemetry,
    settings: Settings,
    build_session_bridge: Callable[..., BridgeBinding],
    storage: BackendStorage,
    vision_memory_runtime: VisionMemoryRuntime | None,
) -> ControlDispatchResult:
    logger.warning(
        "Inbound control type=%s session=%s seq=%s",
        envelope.type,
        envelope.session_id,
        envelope.seq,
    )

    if envelope.type == "session.activate":
        next_active_session = await activate_session(
            envelope=envelope,
            active_session=active_session,
            websocket=websocket,
            send_control=send_control,
            send_server_audio=send_server_audio,
            build_session_bridge=build_session_bridge,
            storage=storage,
            session_memory_retention_days=settings.backend_session_memory_retention_days,
            vision_memory_runtime=vision_memory_runtime,
        )
        return ControlDispatchResult(active_session=next_active_session, handled=True)

    if envelope.type == "session.deactivate":
        if active_session is None:
            return ControlDispatchResult(active_session=active_session, handled=True)
        await deactivate_and_unregister_session(
            active_session=active_session,
            websocket=websocket,
            send_control=send_control,
            storage=storage,
            session_memory_retention_days=settings.backend_session_memory_retention_days,
            vision_memory_runtime=vision_memory_runtime,
        )
        return ControlDispatchResult(active_session=None, handled=True)

    if envelope.type == "session.end_turn":
        if active_session is None:
            logger.info("Ignoring session.end_turn before session.activate")
            return ControlDispatchResult(active_session=active_session, handled=True)
        ignore_reason: str | None = None
        ignore_reason_getter = getattr(
            active_session.bridge,
            "client_end_turn_ignore_reason",
            None,
        )
        if callable(ignore_reason_getter):
            candidate_reason = ignore_reason_getter()
            if isinstance(candidate_reason, str) and candidate_reason:
                ignore_reason = candidate_reason
        if ignore_reason is not None:
            logger.warning(
                "Ignoring session.end_turn session=%s reason=%s",
                active_session.session_id,
                ignore_reason,
            )
            return ControlDispatchResult(active_session=active_session, handled=True)
        logger.warning(
            "Client requested session.end_turn session=%s",
            active_session.session_id,
        )
        try:
            await active_session.bridge.finalize_turn(reason="client_end_turn")
        except RealtimeClientError as exc:
            logger.warning(
                "Failed finalizing turn session=%s: %s",
                active_session.session_id,
                exc,
            )
            await send_control(
                "error",
                {
                    "code": "UPSTREAM_TURN_FINALIZE_FAILED",
                    "message": "Failed to finalize active turn upstream",
                    "retriable": True,
                },
            )
        return ControlDispatchResult(active_session=active_session, handled=True)

    if envelope.type == "health.ping":
        await send_control(
            "health.pong",
            {},
            fallback_session_id=envelope.session_id,
        )
        logger.warning("Health ping session=%s", envelope.session_id)
        return ControlDispatchResult(active_session=active_session, handled=True)

    if envelope.type == "debug.payload_sweep":
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        logger.warning(
            "Debug payload sweep control received session=%s mode=%s index=%s payload_bytes=%s",
            envelope.session_id,
            payload.get("mode"),
            payload.get("index"),
            payload.get("payload_bytes"),
        )
        return ControlDispatchResult(active_session=active_session, handled=True)

    if envelope.type == "client.audio":
        await _handle_text_audio_fallback(
            envelope=envelope,
            active_session=active_session,
            send_control=send_control,
            telemetry=telemetry,
            settings=settings,
        )
        return ControlDispatchResult(active_session=active_session, handled=True)

    if envelope.type == "health.stats":
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        telemetry.log_health_stats(
            envelope_session_id=envelope.session_id,
            payload=payload,
            as_integral_int=as_integral_int,
        )
        return ControlDispatchResult(active_session=active_session, handled=True)

    return ControlDispatchResult(active_session=active_session, handled=False)


async def _handle_text_audio_fallback(
    *,
    envelope: IOSEnvelope,
    active_session: SessionRecord | None,
    send_control: SendControl,
    telemetry: SessionTelemetry,
    settings: Settings,
) -> None:
    if active_session is None:
        logger.info("Ignoring client.audio before session.activate")
        return
    if not settings.backend_allow_text_audio_fallback:
        await send_control(
            "error",
            {
                "code": "TEXT_AUDIO_FALLBACK_DISABLED",
                "message": "client.audio fallback is disabled on this server",
                "retriable": False,
            },
        )
        return

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
        return
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
        return

    if not payload_bytes:
        return

    ack_payload = telemetry.record_text_audio_frame(
        active_session=active_session,
        payload_bytes=payload_bytes,
    )
    if ack_payload is not None:
        await send_control("transport.uplink.ack", ack_payload)

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
