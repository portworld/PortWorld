from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TypedDict

from backend.tools.contracts import ToolDefinition

EnvelopeSender = Callable[[str, dict[str, Any]], Awaitable[None]]
BinarySender = Callable[[int, int, bytes], Awaitable[None]]


class NormalizedRealtimeEventTypes:
    SESSION_READY = "session.ready"
    RESPONSE_AUDIO_DELTA = "response.audio.delta"
    RESPONSE_AUDIO_DONE = "response.audio.done"
    RESPONSE_DONE = "response.done"
    RESPONSE_CREATED = "response.created"
    INPUT_SPEECH_STARTED = "input.speech.started"
    INPUT_SPEECH_STOPPED = "input.speech.stopped"
    INPUT_AUDIO_COMMITTED = "input.audio.committed"
    TOOL_CALL_COMPLETED = "tool.call.completed"
    TOOL_CALL_CANCELLED = "tool.call.cancelled"
    ERROR = "error"
    UNHANDLED = "provider.event.unhandled"


class NormalizedRealtimeEvent(TypedDict, total=False):
    type: str
    payload: dict[str, Any]
    source: str
    raw: Any


NormalizedRealtimeEventStream = AsyncIterator[NormalizedRealtimeEvent]


class RealtimeEventIterator(Protocol):
    def iter_normalized_events(self) -> NormalizedRealtimeEventStream: ...


class RealtimeLifecycleAdapter(Protocol):
    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def initialize_session(
        self,
        *,
        instructions: str | None = None,
        voice: str | None = None,
        tools: Sequence[ToolDefinition] | None = None,
    ) -> None: ...

    async def update_session(self, payload: dict[str, Any]) -> None: ...

    async def append_client_audio(self, pcm16_audio: bytes) -> None: ...

    async def commit_client_turn(self) -> None: ...

    async def create_response(self) -> None: ...

    async def cancel_response(self, *, response_id: str | None = None) -> None: ...

    async def register_tools(self, tools: Sequence[ToolDefinition]) -> None: ...

    async def submit_tool_result(
        self,
        *,
        call_id: str,
        output: str,
    ) -> None: ...

    async def maybe_recover_session_init_error(
        self,
        *,
        code: str,
        message: str,
        tools: Sequence[ToolDefinition] | None = None,
        instructions: str | None = None,
    ) -> bool: ...


class RealtimeAdapterContract(RealtimeLifecycleAdapter, RealtimeEventIterator, Protocol):
    pass


@dataclass(frozen=True, slots=True)
class RealtimeProviderCapabilities:
    streaming_audio_input: bool
    streaming_audio_output: bool
    server_vad: bool
    manual_turn_commit_required: bool
    tool_calling: bool
    tool_result_submission_mode: str
    voice_selection: bool
    interruption_cancel: bool
    startup_validation: bool = True


__all__ = [
    "BinarySender",
    "EnvelopeSender",
    "NormalizedRealtimeEventTypes",
    "NormalizedRealtimeEvent",
    "NormalizedRealtimeEventStream",
    "RealtimeAdapterContract",
    "RealtimeEventIterator",
    "RealtimeLifecycleAdapter",
    "RealtimeProviderCapabilities",
]
