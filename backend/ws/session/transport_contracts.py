from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from backend.ws.session.session_registry import SessionRecord
else:
    SessionRecord = Any


class ClientTransportClosedError(RuntimeError):
    """Raised when the client websocket has already closed."""


class SendControl(Protocol):
    async def __call__(
        self,
        message_type: str,
        payload: dict[str, Any],
        *,
        target: SessionRecord | None = None,
        fallback_session_id: str = "unknown",
    ) -> None: ...


class SendBinary(Protocol):
    async def __call__(self, frame_type: int, ts_ms: int, payload_bytes: bytes) -> None: ...
