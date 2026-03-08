from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from fastapi import WebSocket

from backend.realtime.client import RealtimeClientError
from backend.realtime.factory import BridgeBinding
from backend.ws.contracts import IOSEnvelope
from backend.ws.session_registry import SessionRecord, session_registry
from backend.ws.session_runtime import (
    deactivate_and_unregister_session,
    validate_client_audio_format_payload,
)

logger = logging.getLogger(__name__)

SendControl = Callable[..., Awaitable[None]]
SendBinary = Callable[[int, int, bytes], Awaitable[None]]
BuildSessionBridge = Callable[..., BridgeBinding]


async def activate_session(
    *,
    envelope: IOSEnvelope,
    active_session: SessionRecord | None,
    websocket: WebSocket,
    send_control: SendControl,
    send_server_audio: SendBinary,
    build_session_bridge: BuildSessionBridge,
) -> SessionRecord | None:
    if active_session is not None:
        await deactivate_and_unregister_session(
            active_session=active_session,
            websocket=websocket,
            send_control=send_control,
        )

    format_error = validate_client_audio_format_payload(envelope.payload)
    if format_error is not None:
        await send_control(
            "error",
            format_error,
            fallback_session_id=envelope.session_id,
        )
        return None

    try:
        binding = build_session_bridge(
            session_id=envelope.session_id,
            send_control=send_control,
            send_server_audio=send_server_audio,
        )
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
        return None

    record = await session_registry.register(
        session_id=envelope.session_id,
        websocket=websocket,
        bridge=binding.bridge,
    )
    binding.bind_record(record)

    try:
        await binding.bridge.connect_and_start()
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
        await binding.bridge.close()
        await session_registry.unregister(
            envelope.session_id,
            websocket=websocket,
        )
        return None

    await send_control(
        "session.state",
        {"state": "active"},
        target=record,
    )
    logger.warning("Session activated session=%s", envelope.session_id)
    return record
