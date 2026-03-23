from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket

from backend.core.storage import BackendStorage
from backend.memory.consolidation import DurableMemoryConsolidationRuntime
from backend.vision.runtime import VisionMemoryRuntime
from backend.ws.session.session_registry import SessionRecord, session_registry
from backend.ws.session.transport_contracts import SendControl

MAX_TRACE_TEXT_PREVIEW = 120

logger = logging.getLogger(__name__)


async def deactivate_session(
    *,
    active_session: SessionRecord,
    send_control: SendControl,
    storage: BackendStorage,
    vision_memory_runtime: VisionMemoryRuntime | None = None,
    durable_memory_runtime: DurableMemoryConsolidationRuntime | None = None,
) -> None:
    await send_control(
        "session.state",
        {"state": "ended"},
        target=active_session,
    )
    await _close_finalize_and_mark_ended(
        active_session=active_session,
        storage=storage,
        vision_memory_runtime=vision_memory_runtime,
        durable_memory_runtime=durable_memory_runtime,
    )


async def deactivate_and_unregister_session(
    *,
    active_session: SessionRecord,
    websocket: WebSocket,
    send_control: SendControl,
    storage: BackendStorage,
    vision_memory_runtime: VisionMemoryRuntime | None = None,
    durable_memory_runtime: DurableMemoryConsolidationRuntime | None = None,
    emit_session_state: bool = True,
) -> None:
    try:
        if emit_session_state:
            await deactivate_session(
                active_session=active_session,
                send_control=send_control,
                storage=storage,
                vision_memory_runtime=vision_memory_runtime,
                durable_memory_runtime=durable_memory_runtime,
            )
        else:
            await _close_finalize_and_mark_ended(
                active_session=active_session,
                storage=storage,
                vision_memory_runtime=vision_memory_runtime,
                durable_memory_runtime=durable_memory_runtime,
            )
    except Exception:
        logger.exception(
            "Failed deactivating session session=%s",
            active_session.session_id,
        )
    finally:
        try:
            await session_registry.unregister(
                active_session.session_id,
                websocket=websocket,
            )
        except Exception:
            logger.exception(
                "Failed unregistering session session=%s",
                active_session.session_id,
            )


async def _close_finalize_and_mark_ended(
    *,
    active_session: SessionRecord,
    storage: BackendStorage,
    vision_memory_runtime: VisionMemoryRuntime | None,
    durable_memory_runtime: DurableMemoryConsolidationRuntime | None,
) -> None:
    await active_session.bridge.close()
    if vision_memory_runtime is not None:
        await vision_memory_runtime.finalize_session(session_id=active_session.session_id)
    if durable_memory_runtime is not None:
        await durable_memory_runtime.finalize_session(session_id=active_session.session_id)
    await asyncio.to_thread(
        storage.upsert_session_status,
        session_id=active_session.session_id,
        status="ended",
    )


def trace_ws_message(
    message: dict[str, Any],
    *,
    active_session: SessionRecord | None,
    connection_id: int,
    trace_ws_messages_enabled: bool,
) -> None:
    if not trace_ws_messages_enabled:
        return

    session_id = active_session.session_id if active_session is not None else "unbound"
    message_type = message.get("type", "unknown")
    raw_text = message.get("text")
    raw_bytes = message.get("bytes")
    if isinstance(raw_text, str):
        logger.debug(
            "WS_TRACE connection_id=%s type=%s session=%s text_len=%s preview=%s",
            connection_id,
            message_type,
            session_id,
            len(raw_text),
            sanitize_text_preview(raw_text),
        )
        return
    if isinstance(raw_bytes, (bytes, bytearray, memoryview)):
        return
    logger.debug(
        "WS_TRACE connection_id=%s type=%s session=%s code=%s reason=%s keys=%s",
        connection_id,
        message_type,
        session_id,
        message.get("code"),
        message.get("reason"),
        sorted(message.keys()),
    )


def sanitize_text_preview(raw_text: str) -> str:
    collapsed = " ".join(raw_text.split())
    if len(collapsed) <= MAX_TRACE_TEXT_PREVIEW:
        return collapsed
    return f"{collapsed[:MAX_TRACE_TEXT_PREVIEW]}..."
