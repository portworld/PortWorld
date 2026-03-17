from __future__ import annotations

import json
import logging
import base64
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional
from urllib.parse import urlencode

import websockets
from websockets import exceptions as ws_exceptions

from backend.realtime.contracts import (
    NormalizedRealtimeEvent,
    NormalizedRealtimeEventTypes,
)

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
        trace_events: bool = False,
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
        self._trace_events = trace_events
        self._base_url = base_url

        self._ws: Any | None = None
        self.audio_event_names = RealtimeAudioEventNames()
        self._session_init_schema_mode = "current"
        self._legacy_schema_retry_attempted = False
        self._input_audio_append_count = 0
        self._output_audio_delta_count = 0

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

        if self._trace_events:
            logger.info(
                "Realtime websocket connected endpoint=%s model=%s",
                self.websocket_url,
                self._model,
            )

    async def append_client_audio(self, pcm16_audio: bytes) -> None:
        await self.send_json(
            {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm16_audio).decode("ascii"),
            }
        )

    async def commit_client_turn(self) -> None:
        await self.send_json({"type": "input_audio_buffer.commit"})

    async def create_response(self) -> None:
        await self.send_json({"type": "response.create"})

    async def cancel_response(self, *, response_id: str | None = None) -> None:
        payload: dict[str, Any] = {"type": "response.cancel"}
        if response_id:
            payload["response_id"] = response_id
        await self.send_json(payload)

    async def send_json(self, event: dict[str, Any]) -> None:
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

        if self._trace_events:
            event_type = event.get("type")
            if event_type == "input_audio_buffer.append":
                self._input_audio_append_count += 1
                count = self._input_audio_append_count
                if count == 1:
                    logger.debug(
                        "Upstream send type=%s count=%s",
                        event_type,
                        count,
                    )
            else:
                logger.debug("Upstream send type=%s", event_type)

    async def recv_json(self) -> dict[str, Any]:
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

        normalized = self.normalize_event(event)
        if self._trace_events:
            event_type = normalized.get("type")
            if event_type == self.audio_event_names.delta:
                self._output_audio_delta_count += 1
                delta_len = len(normalized.get("delta", ""))
                if self._output_audio_delta_count == 1:
                    logger.debug(
                        "Upstream recv type=%s delta_b64_len=%s",
                        event_type,
                        delta_len,
                    )
            elif event_type == self.audio_event_names.done:
                logger.debug("Upstream recv type=%s", event_type)
                self._output_audio_delta_count = 0
            elif event_type == "response.output_audio_transcript.delta":
                pass
            elif event_type == "response.output_audio_transcript.done":
                logger.debug("Upstream recv type=%s", event_type)
            else:
                logger.debug("Upstream recv type=%s", event_type)
        return normalized

    async def iter_events(self) -> AsyncIterator[dict[str, Any]]:
        """Async iterator yielding parsed events until websocket closure."""
        while True:
            try:
                yield await self.recv_json()
            except RealtimeClosedError:
                return

    async def iter_normalized_events(self) -> AsyncIterator[NormalizedRealtimeEvent]:
        async for event in self.iter_events():
            yield self._to_normalized_runtime_event(event)

    def normalize_event(self, event: dict[str, Any]) -> dict[str, Any]:
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

    def _to_normalized_runtime_event(
        self,
        event: dict[str, Any],
    ) -> NormalizedRealtimeEvent:
        event_type = event.get("type")
        if not isinstance(event_type, str):
            return {
                "type": NormalizedRealtimeEventTypes.UNHANDLED,
                "payload": event,
                "source": "",
                "raw": event,
            }

        if event_type in {"session.created", "session.updated"}:
            normalized_type = NormalizedRealtimeEventTypes.SESSION_READY
        elif event_type == self.audio_event_names.delta:
            normalized_type = NormalizedRealtimeEventTypes.RESPONSE_AUDIO_DELTA
        elif event_type == self.audio_event_names.done:
            normalized_type = NormalizedRealtimeEventTypes.RESPONSE_AUDIO_DONE
        elif event_type == "response.done":
            normalized_type = NormalizedRealtimeEventTypes.RESPONSE_DONE
        elif event_type == "response.created":
            normalized_type = NormalizedRealtimeEventTypes.RESPONSE_CREATED
        elif event_type == "input_audio_buffer.speech_started":
            normalized_type = NormalizedRealtimeEventTypes.INPUT_SPEECH_STARTED
        elif event_type == "input_audio_buffer.speech_stopped":
            normalized_type = NormalizedRealtimeEventTypes.INPUT_SPEECH_STOPPED
        elif event_type == "input_audio_buffer.committed":
            normalized_type = NormalizedRealtimeEventTypes.INPUT_AUDIO_COMMITTED
        elif event_type in {
            "response.function_call_arguments.done",
            "response.output_item.done",
        } and self._is_function_call_completion_event(event):
            normalized_type = NormalizedRealtimeEventTypes.TOOL_CALL_COMPLETED
        elif event_type == "error":
            normalized_type = NormalizedRealtimeEventTypes.ERROR
        else:
            normalized_type = NormalizedRealtimeEventTypes.UNHANDLED

        return {
            "type": normalized_type,
            "payload": event,
            "source": event_type,
            "raw": event,
        }

    @staticmethod
    def _is_function_call_completion_event(event: dict[str, Any]) -> bool:
        event_type = event.get("type")
        if event_type == "response.function_call_arguments.done":
            return True
        if event_type != "response.output_item.done":
            return False
        item = event.get("item")
        return isinstance(item, dict) and item.get("type") == "function_call"

    async def initialize_session(
        self,
        *,
        instructions: Optional[str] = None,
        voice: Optional[str] = None,
        tools: Optional[Sequence[dict[str, Any]]] = None,
    ) -> None:
        """Initialize realtime session, preferring current schema."""
        event = self._build_session_update_event(
            instructions=instructions,
            voice=voice,
            tools=tools,
            schema_mode=self._session_init_schema_mode,
        )
        await self.send_json(event)

    async def update_session(self, payload: dict[str, Any]) -> None:
        await self.send_json(
            {
                "type": "session.update",
                "session": payload,
            }
        )

    async def retry_initialize_session_with_legacy_schema(
        self,
        *,
        instructions: Optional[str] = None,
        voice: Optional[str] = None,
        tools: Optional[Sequence[dict[str, Any]]] = None,
    ) -> bool:
        """Retry session initialization once with legacy session schema."""
        if self._legacy_schema_retry_attempted:
            return False

        self._legacy_schema_retry_attempted = True
        self._session_init_schema_mode = "legacy"
        await self.initialize_session(
            instructions=instructions,
            voice=voice,
            tools=tools,
        )
        return True

    async def register_tools(self, tools: Sequence[dict[str, Any]]) -> None:
        await self.initialize_session(
            tools=[dict(tool) for tool in tools],
        )

    async def submit_tool_result(
        self,
        *,
        call_id: str,
        output: str,
    ) -> None:
        await self.send_json(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output,
                },
            }
        )

    async def maybe_recover_session_init_error(
        self,
        *,
        code: str,
        message: str,
        tools: Sequence[dict[str, Any]] | None = None,
        instructions: str | None = None,
    ) -> bool:
        if not self._is_session_init_schema_error(code=code, message=message):
            return False
        return await self.retry_initialize_session_with_legacy_schema(
            tools=[dict(tool) for tool in tools] if tools is not None else None,
            instructions=instructions,
        )

    def _build_session_update_event(
        self,
        *,
        instructions: Optional[str],
        voice: Optional[str],
        tools: Optional[Sequence[dict[str, Any]]],
        schema_mode: str,
    ) -> dict[str, Any]:
        resolved_instructions = (
            self._instructions if instructions is None else instructions
        )
        resolved_voice = self._voice if voice is None else voice

        if schema_mode == "legacy":
            session: dict[str, Any] = {
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
                "type": "server_vad",
                "create_response": True,
                "interrupt_response": True,
            }
        if tools:
            session["tools"] = [dict(tool) for tool in tools]
            session["tool_choice"] = "auto"

        return {
            "type": "session.update",
            "session": session,
        }

    @staticmethod
    def _is_session_init_schema_error(*, code: str, message: str) -> bool:
        lower_code = code.strip().lower()
        lower_message = message.strip().lower()

        is_parameter_error = (
            "unknown_parameter" in lower_code
            or "invalid_parameter" in lower_code
            or "unknown parameter" in lower_message
            or "invalid parameter" in lower_message
        )
        if not is_parameter_error:
            return False

        schema_markers = (
            "session.",
            "input_audio_format",
            "output_audio_format",
            "turn_detection",
            "audio.input",
            "audio.output",
            "output_modalities",
        )
        return any(marker in lower_message for marker in schema_markers)

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
