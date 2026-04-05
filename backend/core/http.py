from __future__ import annotations

from fastapi import Request
from starlette.websockets import WebSocket


def client_ip_from_connection(connection: Request | WebSocket) -> str:
    client = connection.client
    if client is None:
        return "unknown"
    host = (client.host or "").strip()
    return host or "unknown"

