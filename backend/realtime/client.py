from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional, Sequence
from urllib.parse import urlencode

from backend.realtime.contracts import (
    NormalizedRealtimeEvent,
    NormalizedRealtimeEventTypes,
)
from backend.realtime.websocket_client import (
    BaseRealtimeWebsocketClient,
    RealtimeClientError,
    RealtimeClosedError,
    RealtimeConnectionError,
    RealtimeProtocolError,
    RealtimeReceiveError,
    RealtimeSendError,
)
from backend.tools.contracts import ToolDefinition

logger = logging.getLogger(__name__)
INPUT_AUDIO_SAMPLE_RATE = 24_000
OUTPUT_AUDIO_SAMPLE_RATE = 24_000


@dataclass(frozen=True)
class RealtimeAudioEventNames:
    """Canonical audio event names used by the client."""

    delta: str = "response.output_audio.delta"
    done: str = "response.output_audio.done"


class OpenAIRealtimeClient(BaseRealtimeWebsocketClient):
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

        super().__init__(trace_events=trace_events)
        self._api_key = api_key
        self._model = model
        self._instructions = instructions
        self._voice = voice
        self._include_turn_detection = include_turn_detection
        self._base_url = base_url

        self.audio_event_names = RealtimeAudioEventNames()
        self._input_audio_append_count = 0
        self._output_audio_delta_count = 0

    @property
    def websocket_url(self) -> str:
        query = urlencode({"model": self._model})
        return f"{self._base_url}?{query}"

    def _connection_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
        }

    def _log_connect_failure(self, exc: Exception) -> None:
        logger.warning(
            "Realtime websocket connect failed type=%s detail=%s endpoint=%s",
            type(exc).__name__,
            str(exc),
            self.websocket_url,
        )

    def _on_connected(self) -> None:
        if self._trace_events:
            logger.info(
                "Realtime websocket connected endpoint=%s model=%s",
                self.websocket_url,
                self._model,
            )

    def _on_send_trace(self, event: dict[str, Any]) -> None:
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
        event = await super().recv_json()
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

    async def initialize_session(
        self,
        *,
        instructions: Optional[str] = None,
        voice: Optional[str] = None,
        tools: Optional[Sequence[ToolDefinition | dict[str, Any]]] = None,
    ) -> None:
        """Initialize realtime session, preferring current schema."""
        event = self._build_session_update_event(
            instructions=instructions,
            voice=voice,
            tools=self._normalize_tools(tools),
        )
        await self.send_json(event)

    async def update_session(self, payload: dict[str, Any]) -> None:
        await self.send_json(payload)

    async def append_client_audio(self, pcm16_audio: bytes) -> None:
        if not pcm16_audio:
            return
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
        if response_id is not None:
            payload["response_id"] = response_id
        await self.send_json(payload)

    async def register_tools(self, tools: Sequence[ToolDefinition]) -> None:
        await self.initialize_session(tools=tools)

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

    async def iter_normalized_events(self) -> AsyncIterator[NormalizedRealtimeEvent]:
        async for event in self.iter_events():
            yield self._to_normalized_runtime_event(event)

    def _to_normalized_runtime_event(
        self,
        event: dict[str, Any],
    ) -> NormalizedRealtimeEvent:
        event_type = event.get("type")
        if not isinstance(event_type, str):
            return {
                "type": NormalizedRealtimeEventTypes.UNHANDLED,
                "payload": event,
                "source": "missing_type",
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
        elif event_type == "input_audio_buffer.speech_started":
            normalized_type = NormalizedRealtimeEventTypes.INPUT_SPEECH_STARTED
        elif event_type == "input_audio_buffer.speech_stopped":
            normalized_type = NormalizedRealtimeEventTypes.INPUT_SPEECH_STOPPED
        elif event_type == "response.created":
            normalized_type = NormalizedRealtimeEventTypes.RESPONSE_CREATED
        elif event_type == "input_audio_buffer.committed":
            normalized_type = NormalizedRealtimeEventTypes.INPUT_AUDIO_COMMITTED
        elif event_type == "error":
            normalized_type = NormalizedRealtimeEventTypes.ERROR
        elif event_type == "response.function_call_arguments.done":
            return {
                "type": NormalizedRealtimeEventTypes.TOOL_CALL_COMPLETED,
                "payload": {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "id": event.get("item_id"),
                        "call_id": event.get("call_id"),
                        "name": event.get("name"),
                        "arguments": event.get("arguments"),
                    },
                    "call_id": event.get("call_id"),
                    "name": event.get("name"),
                    "arguments": event.get("arguments"),
                    "response_id": event.get("response_id"),
                },
                "source": event_type,
                "raw": event,
            }
        elif event_type == "response.output_item.done":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "function_call":
                normalized_type = NormalizedRealtimeEventTypes.TOOL_CALL_COMPLETED
            else:
                normalized_type = NormalizedRealtimeEventTypes.UNHANDLED
        else:
            normalized_type = NormalizedRealtimeEventTypes.UNHANDLED

        return {
            "type": normalized_type,
            "payload": event,
            "source": event_type,
            "raw": event,
        }

    @staticmethod
    def _normalize_tools(
        tools: Sequence[ToolDefinition | dict[str, Any]] | None,
    ) -> list[dict[str, Any]] | None:
        if not tools:
            return None
        normalized: list[dict[str, Any]] = []
        for tool in tools:
            if isinstance(tool, ToolDefinition):
                normalized.append(tool.to_openai_tool())
                continue
            normalized.append(dict(tool))
        return normalized

    def _build_session_update_event(
        self,
        *,
        instructions: Optional[str],
        voice: Optional[str],
        tools: Optional[list[dict[str, Any]]],
    ) -> dict[str, Any]:
        resolved_instructions = (
            self._instructions if instructions is None else instructions
        )
        resolved_voice = self._voice if voice is None else voice

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
            session["tools"] = tools
            session["tool_choice"] = "auto"

        return {
            "type": "session.update",
            "session": session,
        }


__all__ = [
    "INPUT_AUDIO_SAMPLE_RATE",
    "OUTPUT_AUDIO_SAMPLE_RATE",
    "OpenAIRealtimeClient",
    "RealtimeAudioEventNames",
    "RealtimeClientError",
    "RealtimeClosedError",
    "RealtimeConnectionError",
    "RealtimeProtocolError",
    "RealtimeReceiveError",
    "RealtimeSendError",
]
