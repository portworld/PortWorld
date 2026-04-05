from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import ValidationError

from backend.realtime.client import RealtimeClientError
from backend.ws.protocol.audio_format import as_integral_int
from backend.ws.protocol.contracts import IOSEnvelope
from backend.ws.protocol.error_utils import send_error
from backend.ws.session.session_activation import activate_session
from backend.ws.session.session_context import SessionConnectionContext
from backend.ws.session.session_registry import ClientEndTurnPolicyBridge, SessionRecord
from backend.ws.session.session_runtime import (
    deactivate_and_unregister_session,
    sanitize_text_preview,
)
from backend.ws.session.transport_contracts import SendBinary, SendControl
from backend.ws.telemetry import SessionTelemetry

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ControlDispatchResult:
    active_session: SessionRecord | None
    handled: bool


@dataclass(slots=True)
class DispatchContext:
    envelope: IOSEnvelope
    active_session: SessionRecord | None
    connection_context: SessionConnectionContext
    send_control: SendControl
    send_server_audio: SendBinary
    telemetry: SessionTelemetry


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
        await send_error(
            send_control,
            code="INVALID_CONTROL_ENVELOPE",
            message="Invalid control envelope",
            retriable=False,
        )
        return None


async def _handle_session_activate(ctx: DispatchContext) -> ControlDispatchResult:
    next_active_session = await activate_session(
        envelope=ctx.envelope,
        active_session=ctx.active_session,
        websocket=ctx.connection_context.websocket,
        send_control=ctx.send_control,
        send_server_audio=ctx.send_server_audio,
        deps=ctx.connection_context.activation_deps,
    )
    return ControlDispatchResult(active_session=next_active_session, handled=True)


async def _handle_session_deactivate(ctx: DispatchContext) -> ControlDispatchResult:
    if ctx.active_session is None:
        return ControlDispatchResult(active_session=ctx.active_session, handled=True)
    await deactivate_and_unregister_session(
        active_session=ctx.active_session,
        websocket=ctx.connection_context.websocket,
        send_control=ctx.send_control,
        storage=ctx.connection_context.activation_deps.storage,
        vision_memory_runtime=ctx.connection_context.activation_deps.vision_memory_runtime,
        durable_memory_runtime=ctx.connection_context.activation_deps.durable_memory_runtime,
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
        await send_error(
            ctx.send_control,
            code="UPSTREAM_TURN_FINALIZE_FAILED",
            message="Failed to finalize active turn upstream",
            retriable=True,
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
    await send_error(
        ctx.send_control,
        code="UNSUPPORTED_CLIENT_AUDIO",
        message="client.audio is not supported; send binary client audio frames",
        retriable=False,
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
    context: SessionConnectionContext,
    send_control: SendControl,
    send_server_audio: SendBinary,
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
            connection_context=context,
            send_control=send_control,
            send_server_audio=send_server_audio,
            telemetry=context.telemetry,
        )
        return await handler(ctx)

    return ControlDispatchResult(active_session=active_session, handled=False)
