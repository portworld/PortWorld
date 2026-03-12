from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from fastapi import WebSocket

from backend.core.settings import MissingOpenAIAPIKeyError
from backend.core.storage import BackendStorage
from backend.realtime.client import RealtimeClientError
from backend.realtime.factory import BridgeBinding
from backend.vision.runtime import VisionMemoryRuntime
from backend.ws.protocol.contracts import IOSEnvelope
from backend.ws.session.session_registry import (
    SessionAlreadyActiveError,
    SessionRecord,
    session_registry,
)
from backend.ws.session.session_runtime import (
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
    storage: BackendStorage,
    vision_memory_runtime: VisionMemoryRuntime | None,
    trace_ws_messages_enabled: bool,
) -> SessionRecord | None:
    fallback_session = active_session

    format_error = validate_client_audio_format_payload(envelope.payload)
    if format_error is not None:
        await send_control(
            "error",
            format_error,
            fallback_session_id=envelope.session_id,
        )
        return fallback_session

    try:
        binding = build_session_bridge(
            session_id=envelope.session_id,
            send_control=send_control,
            send_server_audio=send_server_audio,
        )
    except MissingOpenAIAPIKeyError:
        await send_control(
            "error",
            {
                "code": "MISSING_OPENAI_API_KEY",
                "message": "Server missing OPENAI_API_KEY",
                "retriable": False,
            },
            fallback_session_id=envelope.session_id,
        )
        return fallback_session

    existing = session_registry.get(envelope.session_id)
    if existing is not None and existing.websocket is not websocket:
        await send_control(
            "error",
            {
                "code": "SESSION_ALREADY_ACTIVE",
                "message": "Session is already active on another connection",
                "retriable": True,
            },
            fallback_session_id=envelope.session_id,
        )
        return fallback_session

    provisional_record = SessionRecord(
        session_id=envelope.session_id,
        websocket=websocket,
        bridge=binding.bridge,
    )
    binding.bind_record(provisional_record)

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
            fallback_session_id=envelope.session_id,
        )
        await _close_bridge_safely(binding=binding, session_id=envelope.session_id)
        return fallback_session
    except Exception:
        logger.exception(
            "Unexpected activation failure session=%s",
            envelope.session_id,
        )
        await send_control(
            "error",
            {
                "code": "UPSTREAM_CONNECT_FAILED",
                "message": "Failed to connect upstream realtime session",
                "retriable": True,
            },
            fallback_session_id=envelope.session_id,
        )
        await _close_bridge_safely(binding=binding, session_id=envelope.session_id)
        return fallback_session

    if active_session is not None and active_session.session_id == envelope.session_id:
        await deactivate_and_unregister_session(
            active_session=active_session,
            websocket=websocket,
            send_control=send_control,
            storage=storage,
            vision_memory_runtime=vision_memory_runtime,
            trace_ws_messages_enabled=trace_ws_messages_enabled,
        )
        fallback_session = None

    try:
        record = await session_registry.register(
            session_id=envelope.session_id,
            websocket=websocket,
            bridge=binding.bridge,
            record=provisional_record,
        )
    except SessionAlreadyActiveError:
        await _close_bridge_safely(binding=binding, session_id=envelope.session_id)
        await send_control(
            "error",
            {
                "code": "SESSION_ALREADY_ACTIVE",
                "message": "Session is already active on another connection",
                "retriable": True,
            },
            fallback_session_id=envelope.session_id,
        )
        return fallback_session

    if active_session is not None and active_session.session_id != envelope.session_id:
        await deactivate_and_unregister_session(
            active_session=active_session,
            websocket=websocket,
            send_control=send_control,
            storage=storage,
            vision_memory_runtime=vision_memory_runtime,
            trace_ws_messages_enabled=trace_ws_messages_enabled,
        )

    await asyncio.to_thread(storage.ensure_session_storage, session_id=envelope.session_id)
    await asyncio.to_thread(
        storage.upsert_session_status,
        session_id=envelope.session_id,
        status="active",
    )

    await send_control(
        "session.state",
        {"state": "active"},
        target=record,
    )
    logger.info("Session activated session=%s", envelope.session_id)
    return record


async def _close_bridge_safely(*, binding: BridgeBinding, session_id: str) -> None:
    try:
        await binding.bridge.close()
    except Exception:
        logger.exception(
            "Failed closing bridge after activation error session=%s",
            session_id,
        )
