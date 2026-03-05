from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, Optional
from urllib.parse import urlencode

import websockets
from websockets import exceptions as ws_exceptions

logger = logging.getLogger(__name__)
INPUT_AUDIO_SAMPLE_RATE = 24_000
OUTPUT_AUDIO_SAMPLE_RATE = 24_000


class RealtimeClientError(Exception):
    """Base error for OpenAI realtime client failures."""


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


@dataclass(frozen=True)
class RealtimeAudioEventNames:
    """Canonical audio event names used by the client."""

    delta: str = "response.output_audio.delta"
    done: str = "response.output_audio.done"


class OpenAIRealtimeClient:
    """Async websocket client for OpenAI Realtime API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        instructions: str,
        voice: str,
        include_turn_detection: bool = False,
        base_url: str = "wss://api.openai.com/v1/realtime",
    ) -> None:
        if not api_key.strip():
            raise ValueError("api_key must be non-empty")
        if not model.strip():
            raise ValueError("model must be non-empty")

        self._api_key = api_key
        self._model = model
        self._instructions = instructions
        self._voice = voice
        self._include_turn_detection = include_turn_detection
        self._base_url = base_url

        self._ws: Any | None = None
        self.audio_event_names = RealtimeAudioEventNames()
        self._session_init_schema_mode = "current"
        self._legacy_schema_retry_attempted = False

    @property
    def is_connected(self) -> bool:
        ws = self._ws
        if ws is None:
            return False
        return not getattr(ws, "closed", False)

    @property
    def websocket_url(self) -> str:
        query = urlencode({"model": self._model})
        return f"{self._base_url}?{query}"

    async def connect(self) -> None:
        """Connect to OpenAI realtime websocket endpoint."""
        if self.is_connected:
            return

        headers = {
            "Authorization": f"Bearer {self._api_key}",
        }

        try:
            # websockets <=11 uses extra_headers, >=12 uses additional_headers.
            try:
                self._ws = await websockets.connect(
                    self.websocket_url,
                    additional_headers=headers,
                )
            except TypeError:
                self._ws = await websockets.connect(
                    self.websocket_url,
                    extra_headers=headers,
                )
        except Exception as exc:
            logger.warning(
                "Realtime websocket connect failed type=%s detail=%s endpoint=%s",
                type(exc).__name__,
                str(exc),
                self.websocket_url,
            )
            raise RealtimeConnectionError(
                f"Failed to connect to realtime endpoint: {self.websocket_url}"
            ) from exc

    async def send_json(self, event: Dict[str, Any]) -> None:
        """Serialize and send a JSON event over websocket."""
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

    async def recv_json(self) -> Dict[str, Any]:
        """Read one websocket message and parse it as JSON event."""
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
                raise RealtimeProtocolError(
                    "Received non-UTF8 websocket frame"
                ) from exc

        if not isinstance(raw_message, str):
            raise RealtimeProtocolError("Received unsupported websocket message type")

        try:
            event = json.loads(raw_message)
        except json.JSONDecodeError as exc:
            raise RealtimeProtocolError(
                "Received invalid JSON from realtime API"
            ) from exc

        if not isinstance(event, dict):
            raise RealtimeProtocolError("Realtime event must be a JSON object")

        return self.normalize_event(event)

    async def iter_events(self) -> AsyncIterator[Dict[str, Any]]:
        """Async iterator yielding parsed events until websocket closure."""
        while True:
            try:
                yield await self.recv_json()
            except RealtimeClosedError:
                return

    def normalize_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize alternate audio event names to canonical names."""
        event_type = event.get("type")
        if not isinstance(event_type, str):
            return event

        normalized_type = self.normalize_audio_event_type(event_type)
        if normalized_type == event_type:
            return event

        normalized = dict(event)
        normalized["type"] = normalized_type
        return normalized

    def normalize_audio_event_type(self, event_type: str) -> str:
        """Map legacy audio event variants to canonical names."""
        if event_type == "response.audio.delta":
            return self.audio_event_names.delta
        if event_type == "response.audio.done":
            return self.audio_event_names.done
        return event_type

    async def initialize_session(
        self,
        *,
        instructions: Optional[str] = None,
        voice: Optional[str] = None,
    ) -> None:
        """Initialize realtime session, preferring current schema."""
        event = self._build_session_update_event(
            instructions=instructions,
            voice=voice,
            schema_mode=self._session_init_schema_mode,
        )
        await self.send_json(event)

    async def retry_initialize_session_with_legacy_schema(
        self,
        *,
        instructions: Optional[str] = None,
        voice: Optional[str] = None,
    ) -> bool:
        """Retry session initialization once with legacy session schema."""
        if self._legacy_schema_retry_attempted:
            return False

        self._legacy_schema_retry_attempted = True
        self._session_init_schema_mode = "legacy"
        await self.initialize_session(instructions=instructions, voice=voice)
        return True

    def _build_session_update_event(
        self,
        *,
        instructions: Optional[str],
        voice: Optional[str],
        schema_mode: str,
    ) -> Dict[str, Any]:
        resolved_instructions = (
            self._instructions if instructions is None else instructions
        )
        resolved_voice = self._voice if voice is None else voice

        if schema_mode == "legacy":
            session: Dict[str, Any] = {
                "type": "realtime",
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "instructions": resolved_instructions,
                "voice": resolved_voice,
            }
            if self._include_turn_detection:
                session["turn_detection"] = {"type": "server_vad"}
            return {
                "type": "session.update",
                "session": session,
            }

        session = {
            "type": "realtime",
            "model": self._model,
            "instructions": resolved_instructions,
            "output_modalities": ["audio"],
            "audio": {
                "input": {
                    "format": {
                        "type": "audio/pcm",
                        "rate": INPUT_AUDIO_SAMPLE_RATE,
                    },
                },
                "output": {
                    "format": {
                        "type": "audio/pcm",
                        "rate": OUTPUT_AUDIO_SAMPLE_RATE,
                    },
                    "voice": resolved_voice,
                },
            },
        }
        if self._include_turn_detection:
            session["audio"]["input"]["turn_detection"] = {
                "type": "semantic_vad",
                "create_response": True,
                "interrupt_response": True,
            }

        return {
            "type": "session.update",
            "session": session,
        }

    async def close(self) -> None:
        """Close websocket connection if open."""
        ws = self._ws
        self._ws = None
        if ws is None:
            return

        try:
            await ws.close()
        except Exception as exc:
            raise RealtimeClientError("Failed to close websocket cleanly") from exc
