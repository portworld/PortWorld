from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from time import time
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from fastapi import WebSocket


class SessionBridge(Protocol):
    async def append_client_audio(self, payload_bytes: bytes) -> None: ...

    async def close(self) -> None: ...


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
    ) -> SessionRecord:
        prior: SessionRecord | None = None
        async with self._lock:
            prior = self._sessions.get(session_id)
            record = SessionRecord(
                session_id=session_id,
                websocket=websocket,
                bridge=bridge,
            )
            self._sessions[session_id] = record

        if prior is not None and prior.bridge is not bridge:
            await prior.bridge.close()

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
