from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TypedDict

EnvelopeSender = Callable[[str, dict[str, Any]], Awaitable[None]]
BinarySender = Callable[[int, int, bytes], Awaitable[None]]


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

    async def initialize_session(self, payload: dict[str, Any]) -> None: ...

    async def update_session(self, payload: dict[str, Any]) -> None: ...

    async def append_client_audio(self, pcm16_audio: bytes) -> None: ...

    async def commit_client_turn(self) -> None: ...

    async def create_response(self) -> None: ...

    async def cancel_response(self) -> None: ...

    async def register_tools(self, tools: Sequence[dict[str, Any]]) -> None: ...

    async def submit_tool_result(
        self,
        *,
        call_id: str,
        result: dict[str, Any],
    ) -> None: ...


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
    "NormalizedRealtimeEvent",
    "NormalizedRealtimeEventStream",
    "RealtimeAdapterContract",
    "RealtimeEventIterator",
    "RealtimeLifecycleAdapter",
    "RealtimeProviderCapabilities",
]
