from __future__ import annotations

from dataclasses import dataclass

from fastapi import WebSocket

from backend.core.runtime import AppRuntime
from backend.ws.session.session_activation import SessionActivationDeps
from backend.ws.session.session_registry import SessionRecord
from backend.ws.telemetry import SessionTelemetry


@dataclass(slots=True)
class SessionConnectionContext:
    runtime: AppRuntime
    websocket: WebSocket
    client_ip: str
    connection_id: int
    telemetry: SessionTelemetry
    activation_deps: SessionActivationDeps
    active_session: SessionRecord | None = None
    server_audio_frame_count: int = 0
    server_audio_total_bytes: int = 0
