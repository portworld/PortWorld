from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import WebSocket
from pydantic import ValidationError

from backend.core.settings import Settings
from backend.core.storage import BackendStorage
from backend.memory.consolidation import DurableMemoryConsolidationRuntime
from backend.realtime.client import RealtimeClientError
from backend.realtime.factory import BridgeBinding
from backend.vision.runtime import VisionMemoryRuntime
from backend.ws.protocol.contracts import IOSEnvelope
from backend.ws.session.session_activation import activate_session
from backend.ws.session.session_registry import ClientEndTurnPolicyBridge, SessionRecord
from backend.ws.session.session_runtime import (
    as_integral_int,
    deactivate_and_unregister_session,
    sanitize_text_preview,
)
from backend.ws.telemetry import SessionTelemetry

if TYPE_CHECKING:
    from typing import Any

logger = logging.getLogger(__name__)

SendControl = Callable[..., Awaitable[None]]
SendBinary = Callable[[int, int, bytes], Awaitable[None]]


@dataclass(slots=True)
class ControlDispatchResult:
    active_session: SessionRecord | None
    handled: bool


@dataclass(slots=True)
class DispatchContext:
    envelope: IOSEnvelope
    active_session: SessionRecord | None
    websocket: WebSocket
    send_control: SendControl
    send_server_audio: SendBinary
    telemetry: SessionTelemetry
    settings: Settings
    build_session_bridge: Callable[..., BridgeBinding]
    storage: BackendStorage
    vision_memory_runtime: VisionMemoryRuntime | None
    durable_memory_runtime: DurableMemoryConsolidationRuntime | None


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


async def _handle_session_activate(ctx: DispatchContext) -> ControlDispatchResult:
    next_active_session = await activate_session(
        envelope=ctx.envelope,
        active_session=ctx.active_session,
        websocket=ctx.websocket,
        send_control=ctx.send_control,
        send_server_audio=ctx.send_server_audio,
        build_session_bridge=ctx.build_session_bridge,
        storage=ctx.storage,
        vision_memory_runtime=ctx.vision_memory_runtime,
        durable_memory_runtime=ctx.durable_memory_runtime,
        trace_ws_messages_enabled=ctx.settings.backend_debug_trace_ws_messages,
    )
    return ControlDispatchResult(active_session=next_active_session, handled=True)


async def _handle_session_deactivate(ctx: DispatchContext) -> ControlDispatchResult:
    if ctx.active_session is None:
        return ControlDispatchResult(active_session=ctx.active_session, handled=True)
    await deactivate_and_unregister_session(
        active_session=ctx.active_session,
        websocket=ctx.websocket,
        send_control=ctx.send_control,
        storage=ctx.storage,
        vision_memory_runtime=ctx.vision_memory_runtime,
        durable_memory_runtime=ctx.durable_memory_runtime,
        trace_ws_messages_enabled=ctx.settings.backend_debug_trace_ws_messages,
    )
    return ControlDispatchResult(active_session=None, handled=True)


async def _handle_session_end_turn(ctx: DispatchContext) -> ControlDispatchResult:
    if ctx.active_session is None:
        logger.info("Ignoring session.end_turn before session.activate")
        return ControlDispatchResult(active_session=ctx.active_session, handled=True)
    ignore_reason: str | None = None
    if isinstance(ctx.active_session.bridge, ClientEndTurnPolicyBridge):
        candidate_reason = ctx.active_session.bridge.client_end_turn_ignore_reason()
        if isinstance(candidate_reason, str) and candidate_reason:
            ignore_reason = candidate_reason
    if ignore_reason is not None:
        logger.info(
            "Ignoring session.end_turn session=%s reason=%s",
            ctx.active_session.session_id,
            ignore_reason,
        )
        return ControlDispatchResult(active_session=ctx.active_session, handled=True)
    logger.info(
        "Client requested session.end_turn session=%s",
        ctx.active_session.session_id,
    )
    try:
        await ctx.active_session.bridge.finalize_turn(reason="client_end_turn")
    except RealtimeClientError as exc:
        logger.warning(
            "Failed finalizing turn session=%s: %s",
            ctx.active_session.session_id,
            exc,
        )
        await ctx.send_control(
            "error",
            {
                "code": "UPSTREAM_TURN_FINALIZE_FAILED",
                "message": "Failed to finalize active turn upstream",
                "retriable": True,
            },
        )
    return ControlDispatchResult(active_session=ctx.active_session, handled=True)


async def _handle_health_ping(ctx: DispatchContext) -> ControlDispatchResult:
    await ctx.send_control(
        "health.pong",
        {},
        fallback_session_id=ctx.envelope.session_id,
    )
    logger.debug("Health ping session=%s", ctx.envelope.session_id)
    return ControlDispatchResult(active_session=ctx.active_session, handled=True)


async def _handle_client_audio(ctx: DispatchContext) -> ControlDispatchResult:
    await ctx.send_control(
        "error",
        {
            "code": "UNSUPPORTED_CLIENT_AUDIO",
            "message": "client.audio is not supported; send binary client audio frames",
            "retriable": False,
        },
    )
    return ControlDispatchResult(active_session=ctx.active_session, handled=True)


async def _handle_health_stats(ctx: DispatchContext) -> ControlDispatchResult:
    payload = ctx.envelope.payload if isinstance(ctx.envelope.payload, dict) else {}
    ctx.telemetry.log_health_stats(
        envelope_session_id=ctx.envelope.session_id,
        payload=payload,
        as_integral_int=as_integral_int,
    )
    return ControlDispatchResult(active_session=ctx.active_session, handled=True)


_ENVELOPE_HANDLERS: dict[str, Callable[[DispatchContext], Awaitable[ControlDispatchResult]]] = {
    "session.activate": _handle_session_activate,
    "session.deactivate": _handle_session_deactivate,
    "session.end_turn": _handle_session_end_turn,
    "health.ping": _handle_health_ping,
    "client.audio": _handle_client_audio,
    "health.stats": _handle_health_stats,
}


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
    durable_memory_runtime: DurableMemoryConsolidationRuntime | None,
) -> ControlDispatchResult:
    logger.debug(
        "Inbound control type=%s session=%s seq=%s",
        envelope.type,
        envelope.session_id,
        envelope.seq,
    )

    handler = _ENVELOPE_HANDLERS.get(envelope.type)
    if handler is not None:
        ctx = DispatchContext(
            envelope=envelope,
            active_session=active_session,
            websocket=websocket,
            send_control=send_control,
            send_server_audio=send_server_audio,
            telemetry=telemetry,
            settings=settings,
            build_session_bridge=build_session_bridge,
            storage=storage,
            vision_memory_runtime=vision_memory_runtime,
            durable_memory_runtime=durable_memory_runtime,
        )
        return await handler(ctx)

    return ControlDispatchResult(active_session=active_session, handled=False)
