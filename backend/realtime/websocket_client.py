from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping
from typing import Any

import websockets
from websockets import exceptions as ws_exceptions


class RealtimeClientError(Exception):
    """Base error for realtime websocket client failures."""


class RealtimeConnectionError(RealtimeClientError):
    """Raised when connecting to the realtime endpoint fails."""


class RealtimeProtocolError(RealtimeClientError):
    """Raised when websocket payloads are invalid for the expected protocol."""


class RealtimeSendError(RealtimeClientError):
    """Raised when sending an event over the websocket fails."""


class RealtimeReceiveError(RealtimeClientError):
    """Raised when receiving an event over the websocket fails."""


class RealtimeClosedError(RealtimeClientError):
    """Raised when trying to use a closed or uninitialized connection."""


class BaseRealtimeWebsocketClient(ABC):
    def __init__(self, *, trace_events: bool = False) -> None:
        self._trace_events = trace_events
        self._ws: Any | None = None

    @property
    @abstractmethod
    def websocket_url(self) -> str:
        raise NotImplementedError

    @property
    def is_connected(self) -> bool:
        ws = self._ws
        if ws is None:
            return False
        return not getattr(ws, "closed", False)

    async def connect(self) -> None:
        if self.is_connected:
            return

        headers = dict(self._connection_headers())
        kwargs = dict(self._connection_kwargs())

        try:
            if headers:
                try:
                    self._ws = await websockets.connect(
                        self.websocket_url,
                        additional_headers=headers,
                        **kwargs,
                    )
                except TypeError:
                    self._ws = await websockets.connect(
                        self.websocket_url,
                        extra_headers=headers,
                        **kwargs,
                    )
            else:
                self._ws = await websockets.connect(self.websocket_url, **kwargs)
        except Exception as exc:
            self._log_connect_failure(exc)
            raise RealtimeConnectionError(
                f"Failed to connect to realtime endpoint: {self._connection_error_endpoint()}"
            ) from exc

        self._on_connected()

    async def send_json(self, event: dict[str, Any]) -> None:
        ws = self._ws
        if ws is None or getattr(ws, "closed", False):
            raise RealtimeClosedError("Websocket is not connected")

        try:
            payload = json.dumps(event)
        except (TypeError, ValueError) as exc:
            raise RealtimeProtocolError("Event is not JSON serializable") from exc

        try:
            await ws.send(payload)
        except ws_exceptions.ConnectionClosed as exc:
            raise RealtimeClosedError("Websocket is closed") from exc
        except Exception as exc:
            raise RealtimeSendError("Failed to send event") from exc

        if self._trace_events:
            self._on_send_trace(event)

    async def recv_json(self) -> dict[str, Any]:
        ws = self._ws
        if ws is None or getattr(ws, "closed", False):
            raise RealtimeClosedError("Websocket is not connected")

        try:
            raw_message = await ws.recv()
        except ws_exceptions.ConnectionClosed as exc:
            raise RealtimeClosedError("Websocket is closed") from exc
        except Exception as exc:
            raise RealtimeReceiveError("Failed to receive event") from exc

        if isinstance(raw_message, bytes):
            try:
                raw_message = raw_message.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise RealtimeProtocolError("Received non-UTF8 websocket frame") from exc

        if not isinstance(raw_message, str):
            raise RealtimeProtocolError("Received unsupported websocket message type")

        try:
            event = json.loads(raw_message)
        except json.JSONDecodeError as exc:
            raise RealtimeProtocolError("Received invalid JSON from realtime API") from exc

        if not isinstance(event, dict):
            raise RealtimeProtocolError("Realtime event must be a JSON object")

        if self._trace_events:
            self._on_recv_trace(event)
        return event

    async def iter_events(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            try:
                yield await self.recv_json()
            except RealtimeClosedError:
                return

    async def close(self) -> None:
        ws = self._ws
        self._ws = None
        if ws is None:
            return

        try:
            await ws.close()
        except Exception as exc:
            raise RealtimeClientError("Failed to close websocket cleanly") from exc

    def _connection_headers(self) -> Mapping[str, str]:
        return {}

    def _connection_kwargs(self) -> Mapping[str, Any]:
        return {}

    def _connection_error_endpoint(self) -> str:
        return self.websocket_url

    def _log_connect_failure(self, exc: Exception) -> None:
        _ = exc

    def _on_connected(self) -> None:
        return

    def _on_send_trace(self, event: dict[str, Any]) -> None:
        _ = event

    def _on_recv_trace(self, event: dict[str, Any]) -> None:
        _ = event
