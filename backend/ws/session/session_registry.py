from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from time import time
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from fastapi import WebSocket

logger = logging.getLogger(__name__)


class SessionBridge(Protocol):
    async def append_client_audio(self, payload_bytes: bytes) -> None: ...

    async def finalize_turn(self, *, reason: str = "client_end_turn") -> None: ...

    async def close(self) -> None: ...


@runtime_checkable
class CaptureSummaryBridge(Protocol):
    def capture_summary(self) -> dict[str, object]: ...


@runtime_checkable
class ClientEndTurnPolicyBridge(Protocol):
    def client_end_turn_ignore_reason(self) -> str | None: ...


class SessionAlreadyActiveError(RuntimeError):
    """Raised when another websocket already owns the requested session."""


@dataclass(slots=True)
class SessionRecord:
    session_id: str
    websocket: "WebSocket"
    bridge: SessionBridge
    connected_at: float = field(default_factory=time)
    _outbound_seq: int = 0

    def next_seq(self) -> int:
        self._outbound_seq += 1
        return self._outbound_seq


class SessionRegistry:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionRecord] = {}
        self._lock = asyncio.Lock()

    async def register(
        self,
        *,
        session_id: str,
        websocket: "WebSocket",
        bridge: SessionBridge,
        record: SessionRecord | None = None,
    ) -> SessionRecord:
        prior_to_close: SessionBridge | None = None
        async with self._lock:
            prior = self._sessions.get(session_id)
            if prior is not None and prior.websocket is not websocket:
                raise SessionAlreadyActiveError(
                    f"session_id={session_id!r} is already active on another websocket"
                )
            record = record or SessionRecord(
                session_id=session_id,
                websocket=websocket,
                bridge=bridge,
            )
            if record.session_id != session_id:
                raise ValueError("record.session_id must match session_id")
            if record.websocket is not websocket:
                raise ValueError("record.websocket must match websocket")
            if record.bridge is not bridge:
                raise ValueError("record.bridge must match bridge")
            self._sessions[session_id] = record
            if prior is not None and prior.bridge is not bridge:
                prior_to_close = prior.bridge

        if prior_to_close is not None:
            try:
                await prior_to_close.close()
            except Exception:
                logger.exception(
                    "Failed closing replaced prior bridge session=%s",
                    session_id,
                )

        return record

    async def unregister(
        self,
        session_id: str,
        *,
        websocket: "WebSocket | None" = None,
    ) -> SessionRecord | None:
        async with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                return None
            if websocket is not None and record.websocket is not websocket:
                return None
            return self._sessions.pop(session_id, None)

    def get(self, session_id: str) -> SessionRecord | None:
        return self._sessions.get(session_id)


session_registry = SessionRegistry()
