from __future__ import annotations

import secrets

from fastapi import HTTPException, Request
from starlette.websockets import WebSocket

from backend.core.settings import Settings


def has_backend_bearer_token(settings: Settings) -> bool:
    return bool(settings.backend_bearer_token)


def require_http_bearer_auth(*, request: Request, settings: Settings) -> None:
    if not has_backend_bearer_token(settings):
        return
    if not _is_authorized(auth_header=request.headers.get("authorization"), settings=settings):
        raise HTTPException(status_code=401, detail="Unauthorized")


async def reject_ws_if_unauthorized(*, websocket: WebSocket, settings: Settings) -> bool:
    if not has_backend_bearer_token(settings):
        return False
    if _is_authorized(auth_header=websocket.headers.get("authorization"), settings=settings):
        return False
    await websocket.close(code=1008, reason="Unauthorized")
    return True


def _is_authorized(*, auth_header: str | None, settings: Settings) -> bool:
    expected_token = settings.backend_bearer_token
    if not expected_token:
        return True
    provided_token = _extract_bearer_token(auth_header)
    if provided_token is None:
        return False
    return secrets.compare_digest(provided_token, expected_token)


def _extract_bearer_token(auth_header: str | None) -> str | None:
    if auth_header is None:
        return None
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    normalized_token = token.strip()
    if not normalized_token:
        return None
    return normalized_token
