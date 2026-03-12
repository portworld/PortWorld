from __future__ import annotations

import itertools
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.core.auth import reject_ws_if_unauthorized
from backend.core.http import client_ip_from_connection
from backend.core.runtime import get_app_runtime
from backend.ws.session_context import SessionConnectionContext
from backend.ws.session_loop import process_next_websocket_message
from backend.ws.session_runtime import deactivate_and_unregister_session
from backend.ws.session_transport import make_send_control, make_send_server_audio
from backend.ws.telemetry import SessionTelemetry

router = APIRouter()
logger = logging.getLogger(__name__)
_connection_ids = itertools.count(1)


@router.websocket("/ws/session")
async def ws_session(websocket: WebSocket) -> None:
    runtime = get_app_runtime(websocket.app)
    client_ip = client_ip_from_connection(websocket)
    connect_rate_decision = await runtime.limit_ws_connect(client_ip=client_ip)
    if not connect_rate_decision.allowed:
        logger.warning(
            "Rejected rate-limited websocket connect ip=%s retry_after_seconds=%s",
            client_ip,
            connect_rate_decision.retry_after_seconds,
        )
        await websocket.close(code=1013, reason="Rate limited")
        return
    if await reject_ws_if_unauthorized(websocket=websocket, settings=runtime.settings):
        logger.warning("Rejected unauthorized websocket session request")
        return

    await websocket.accept()
    connection_id = next(_connection_ids)
    context = SessionConnectionContext(
        runtime=runtime,
        websocket=websocket,
        client_ip=client_ip,
        connection_id=connection_id,
        telemetry=SessionTelemetry(
            connection_id=connection_id,
            uplink_ack_every_n_frames=runtime.settings.backend_uplink_ack_every_n_frames,
        ),
    )
    send_control = make_send_control(context)
    send_server_audio = make_send_server_audio(context)

    try:
        while await process_next_websocket_message(
            context=context,
            send_control=send_control,
            send_server_audio=send_server_audio,
        ):
            pass
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    finally:
        if context.active_session is not None:
            await deactivate_and_unregister_session(
                active_session=context.active_session,
                websocket=websocket,
                send_control=send_control,
                storage=runtime.storage,
                session_memory_retention_days=runtime.settings.backend_session_memory_retention_days,
                vision_memory_runtime=runtime.vision_memory_runtime,
                emit_session_state=False,
                trace_ws_messages_enabled=runtime.settings.backend_debug_trace_ws_messages,
            )
