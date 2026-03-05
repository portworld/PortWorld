from __future__ import annotations

import base64
from typing import Any

from backend.bridge import IOSRealtimeBridge
from backend.frame_codec import SERVER_AUDIO_FRAME_TYPE


class FakeUpstreamClient:
    def __init__(self) -> None:
        self.connected = False
        self.initialized = False
        self.closed = False
        self.sent_events: list[dict[str, Any]] = []
        self.legacy_retry_calls = 0
        self.legacy_retry_result = True

    async def connect(self) -> None:
        self.connected = True

    async def initialize_session(self) -> None:
        self.initialized = True

    async def send_json(self, event: dict[str, Any]) -> None:
        self.sent_events.append(event)

    async def retry_initialize_session_with_legacy_schema(self) -> bool:
        self.legacy_retry_calls += 1
        return self.legacy_retry_result

    async def iter_events(self):
        if False:
            yield {}

    async def close(self) -> None:
        self.closed = True


def test_append_client_audio_forwards_input_audio_buffer_append() -> None:
    envelopes: list[tuple[str, dict[str, Any]]] = []
    frames: list[tuple[int, int, bytes]] = []
    upstream = FakeUpstreamClient()
    bridge = IOSRealtimeBridge(
        session_id="sess_test",
        upstream_client=upstream,
        send_envelope=lambda m_type, payload: _capture_envelope(
            envelopes, m_type, payload
        ),
        send_binary_frame=lambda frame_type, ts_ms, payload: _capture_frame(
            frames, frame_type, ts_ms, payload
        ),
        manual_turn_fallback_enabled=False,
    )

    payload = b"\x01\x02\x03\x04"
    _run(bridge.append_client_audio(payload))

    assert not envelopes
    assert not frames
    assert len(upstream.sent_events) == 1
    assert upstream.sent_events[0]["type"] == "input_audio_buffer.append"
    assert base64.b64decode(upstream.sent_events[0]["audio"]) == payload


def test_upstream_audio_flow_emits_thinking_start_binary_stop() -> None:
    envelopes: list[tuple[str, dict[str, Any]]] = []
    frames: list[tuple[int, int, bytes]] = []
    upstream = FakeUpstreamClient()
    bridge = IOSRealtimeBridge(
        session_id="sess_test",
        upstream_client=upstream,
        send_envelope=lambda m_type, payload: _capture_envelope(
            envelopes, m_type, payload
        ),
        send_binary_frame=lambda frame_type, ts_ms, payload: _capture_frame(
            frames, frame_type, ts_ms, payload
        ),
        manual_turn_fallback_enabled=False,
    )

    audio_bytes = b"\x10\x20\x30"
    audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
    _run(bridge._handle_upstream_event({"type": "input_audio_buffer.speech_started"}))
    _run(
        bridge._handle_upstream_event(
            {
                "type": "response.output_audio.delta",
                "response_id": "resp_1",
                "delta": audio_b64,
            }
        )
    )
    _run(
        bridge._handle_upstream_event(
            {
                "type": "response.output_audio.done",
                "response_id": "resp_1",
            }
        )
    )

    assert ("assistant.thinking", {"status": "thinking"}) in envelopes
    assert (
        "assistant.playback.control",
        {"command": "start_response", "response_id": "resp_1"},
    ) in envelopes
    assert (
        "assistant.playback.control",
        {"command": "stop_response", "response_id": "resp_1"},
    ) in envelopes
    assert len(frames) == 1
    assert frames[0][0] == SERVER_AUDIO_FRAME_TYPE
    assert frames[0][2] == audio_bytes


def test_upstream_error_event_maps_to_ios_error_envelope() -> None:
    envelopes: list[tuple[str, dict[str, Any]]] = []
    frames: list[tuple[int, int, bytes]] = []
    upstream = FakeUpstreamClient()
    bridge = IOSRealtimeBridge(
        session_id="sess_test",
        upstream_client=upstream,
        send_envelope=lambda m_type, payload: _capture_envelope(
            envelopes, m_type, payload
        ),
        send_binary_frame=lambda frame_type, ts_ms, payload: _capture_frame(
            frames, frame_type, ts_ms, payload
        ),
        manual_turn_fallback_enabled=False,
    )

    _run(
        bridge._handle_upstream_event(
            {
                "type": "error",
                "error": {
                    "code": "RATE_LIMITED",
                    "message": "Too many requests",
                    "retriable": True,
                },
            }
        )
    )

    assert (
        "error",
        {"code": "RATE_LIMITED", "message": "Too many requests", "retriable": True},
    ) in envelopes
    assert not frames


def test_session_schema_error_retries_legacy_and_suppresses_error() -> None:
    envelopes: list[tuple[str, dict[str, Any]]] = []
    frames: list[tuple[int, int, bytes]] = []
    upstream = FakeUpstreamClient()
    bridge = IOSRealtimeBridge(
        session_id="sess_test",
        upstream_client=upstream,
        send_envelope=lambda m_type, payload: _capture_envelope(
            envelopes, m_type, payload
        ),
        send_binary_frame=lambda frame_type, ts_ms, payload: _capture_frame(
            frames, frame_type, ts_ms, payload
        ),
        manual_turn_fallback_enabled=False,
    )

    _run(
        bridge._handle_upstream_event(
            {
                "type": "error",
                "error": {
                    "code": "unknown_parameter",
                    "message": "Unknown parameter: 'session.input_audio_format'.",
                    "retriable": False,
                },
            }
        )
    )

    assert upstream.legacy_retry_calls == 1
    assert not envelopes
    assert not frames


def test_session_schema_error_forwards_when_legacy_retry_not_possible() -> None:
    envelopes: list[tuple[str, dict[str, Any]]] = []
    frames: list[tuple[int, int, bytes]] = []
    upstream = FakeUpstreamClient()
    upstream.legacy_retry_result = False
    bridge = IOSRealtimeBridge(
        session_id="sess_test",
        upstream_client=upstream,
        send_envelope=lambda m_type, payload: _capture_envelope(
            envelopes, m_type, payload
        ),
        send_binary_frame=lambda frame_type, ts_ms, payload: _capture_frame(
            frames, frame_type, ts_ms, payload
        ),
        manual_turn_fallback_enabled=False,
    )

    _run(
        bridge._handle_upstream_event(
            {
                "type": "error",
                "error": {
                    "code": "unknown_parameter",
                    "message": "Unknown parameter: 'session.input_audio_format'.",
                    "retriable": False,
                },
            }
        )
    )

    assert upstream.legacy_retry_calls == 1
    assert (
        "error",
        {
            "code": "unknown_parameter",
            "message": "Unknown parameter: 'session.input_audio_format'.",
            "retriable": False,
        },
    ) in envelopes
    assert not frames


def test_speech_stopped_triggers_manual_commit_and_response_create_once() -> None:
    upstream = FakeUpstreamClient()
    bridge = IOSRealtimeBridge(
        session_id="sess_test",
        upstream_client=upstream,
        send_envelope=lambda *_args, **_kwargs: _noop_async(),
        send_binary_frame=lambda *_args, **_kwargs: _noop_async(),
        manual_turn_fallback_enabled=True,
        manual_turn_fallback_delay_ms=5_000,
    )

    _run(bridge.append_client_audio(b"\x01\x02"))
    _run(bridge._handle_upstream_event({"type": "input_audio_buffer.speech_stopped"}))
    _run(bridge._handle_upstream_event({"type": "input_audio_buffer.speech_stopped"}))

    assert [event["type"] for event in upstream.sent_events] == [
        "input_audio_buffer.append",
        "input_audio_buffer.commit",
        "response.create",
    ]


def test_manual_turn_fallback_idle_timeout_triggers_once() -> None:
    upstream = FakeUpstreamClient()
    bridge = IOSRealtimeBridge(
        session_id="sess_test",
        upstream_client=upstream,
        send_envelope=lambda *_args, **_kwargs: _noop_async(),
        send_binary_frame=lambda *_args, **_kwargs: _noop_async(),
        manual_turn_fallback_enabled=True,
        manual_turn_fallback_delay_ms=10,
    )

    _run(bridge.append_client_audio(b"\x01\x02"))
    _run(_sleep(0.03))
    _run(bridge.append_client_audio(b"\x03\x04"))

    assert [event["type"] for event in upstream.sent_events] == [
        "input_audio_buffer.append",
        "input_audio_buffer.commit",
        "response.create",
    ]


def test_response_created_prevents_manual_turn_finalize() -> None:
    upstream = FakeUpstreamClient()
    bridge = IOSRealtimeBridge(
        session_id="sess_test",
        upstream_client=upstream,
        send_envelope=lambda *_args, **_kwargs: _noop_async(),
        send_binary_frame=lambda *_args, **_kwargs: _noop_async(),
        manual_turn_fallback_enabled=True,
        manual_turn_fallback_delay_ms=5_000,
    )

    _run(bridge.append_client_audio(b"\x01\x02"))
    _run(bridge._handle_upstream_event({"type": "response.created"}))
    _run(bridge._handle_upstream_event({"type": "input_audio_buffer.speech_stopped"}))

    assert [event["type"] for event in upstream.sent_events] == [
        "input_audio_buffer.append",
    ]


async def _capture_envelope(
    sink: list[tuple[str, dict[str, Any]]],
    message_type: str,
    payload: dict[str, Any],
) -> None:
    sink.append((message_type, payload))


async def _capture_frame(
    sink: list[tuple[int, int, bytes]],
    frame_type: int,
    ts_ms: int,
    payload: bytes,
) -> None:
    sink.append((frame_type, ts_ms, payload))


async def _noop_async() -> None:
    return


async def _sleep(duration_seconds: float) -> None:
    import asyncio

    await asyncio.sleep(duration_seconds)


def _run(awaitable):
    import asyncio

    return asyncio.run(awaitable)
