from __future__ import annotations

import asyncio

from backend.openai_realtime_client import OpenAIRealtimeClient


class RecordingRealtimeClient(OpenAIRealtimeClient):
    def __init__(self, *, include_turn_detection: bool) -> None:
        super().__init__(
            api_key="test-key",
            model="gpt-realtime",
            instructions="base instructions",
            voice="ash",
            include_turn_detection=include_turn_detection,
        )
        self.sent_events: list[dict[str, object]] = []

    async def send_json(self, event: dict[str, object]) -> None:
        self.sent_events.append(event)


def test_initialize_session_omits_turn_detection_by_default() -> None:
    client = RecordingRealtimeClient(include_turn_detection=False)

    asyncio.run(client.initialize_session())

    assert len(client.sent_events) == 1
    event = client.sent_events[0]
    session = event["session"]

    assert event["type"] == "session.update"
    assert isinstance(session, dict)
    assert session["type"] == "realtime"
    assert session["model"] == "gpt-realtime"
    assert session["output_modalities"] == ["audio"]
    assert session["audio"]["input"]["format"] == {"type": "audio/pcm", "rate": 24000}
    assert session["audio"]["output"]["format"] == {"type": "audio/pcm", "rate": 24000}
    assert session["audio"]["output"]["voice"] == "ash"
    assert "turn_detection" not in session["audio"]["input"]


def test_initialize_session_includes_turn_detection_when_enabled() -> None:
    client = RecordingRealtimeClient(include_turn_detection=True)

    asyncio.run(client.initialize_session(instructions="custom", voice="verse"))

    assert len(client.sent_events) == 1
    event = client.sent_events[0]
    session = event["session"]

    assert event["type"] == "session.update"
    assert isinstance(session, dict)
    assert session["audio"]["input"]["turn_detection"] == {
        "type": "semantic_vad",
        "create_response": True,
        "interrupt_response": True,
    }
    assert session["instructions"] == "custom"
    assert session["audio"]["output"]["voice"] == "verse"


def test_retry_initialize_session_with_legacy_schema_runs_once() -> None:
    client = RecordingRealtimeClient(include_turn_detection=True)

    asyncio.run(client.initialize_session())
    did_retry = asyncio.run(client.retry_initialize_session_with_legacy_schema())
    did_retry_again = asyncio.run(client.retry_initialize_session_with_legacy_schema())

    assert did_retry is True
    assert did_retry_again is False
    assert len(client.sent_events) == 2

    legacy_event = client.sent_events[1]
    session = legacy_event["session"]

    assert legacy_event["type"] == "session.update"
    assert session["type"] == "realtime"
    assert session["input_audio_format"] == "pcm16"
    assert session["output_audio_format"] == "pcm16"
    assert session["turn_detection"] == {"type": "server_vad"}
