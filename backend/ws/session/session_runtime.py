from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import WebSocket

from backend.core.storage import BackendStorage
from backend.memory.consolidation import DurableMemoryConsolidationRuntime
from backend.vision.runtime import VisionMemoryRuntime
from backend.ws.session.session_registry import SessionRecord, session_registry

EXPECTED_CLIENT_AUDIO_ENCODING = "pcm_s16le"
EXPECTED_CLIENT_AUDIO_CHANNELS = 1
EXPECTED_CLIENT_AUDIO_SAMPLE_RATE = 24_000
MAX_TRACE_TEXT_PREVIEW = 120

logger = logging.getLogger(__name__)


def validate_client_audio_format_payload(
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
    channels = as_integral_int(raw_format.get("channels"))
    sample_rate = as_integral_int(raw_format.get("sample_rate"))

    if not isinstance(encoding, str) or channels is None or sample_rate is None:
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


def as_integral_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


async def deactivate_session(
    *,
    active_session: SessionRecord,
    send_control: Callable[..., Awaitable[None]],
    storage: BackendStorage,
    vision_memory_runtime: VisionMemoryRuntime | None = None,
    durable_memory_runtime: DurableMemoryConsolidationRuntime | None = None,
    trace_ws_messages_enabled: bool = False,
) -> None:
    _ = trace_ws_messages_enabled
    await send_control(
        "session.state",
        {"state": "ended"},
        target=active_session,
    )
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


async def deactivate_and_unregister_session(
    *,
    active_session: SessionRecord,
    websocket: WebSocket,
    send_control: Callable[..., Awaitable[None]],
    storage: BackendStorage,
    vision_memory_runtime: VisionMemoryRuntime | None = None,
    durable_memory_runtime: DurableMemoryConsolidationRuntime | None = None,
    emit_session_state: bool = True,
    trace_ws_messages_enabled: bool = False,
) -> None:
    try:
        if emit_session_state:
            await deactivate_session(
                active_session=active_session,
                send_control=send_control,
                storage=storage,
                vision_memory_runtime=vision_memory_runtime,
                durable_memory_runtime=durable_memory_runtime,
                trace_ws_messages_enabled=trace_ws_messages_enabled,
            )
        else:
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
