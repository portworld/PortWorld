from __future__ import annotations

import asyncio
import base64
from typing import Any

from backend.bridge import IOSRealtimeBridge
from backend.frame_codec import SERVER_AUDIO_FRAME_TYPE
from backend.openai_realtime_client import RealtimeClientError


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


class EventfulFakeUpstreamClient(FakeUpstreamClient):
    def __init__(self, events: list[dict[str, Any]]) -> None:
        super().__init__()
        self.events = events

    async def iter_events(self):
        for event in self.events:
            await _sleep(0)
            yield event


class BlockingAppendFakeUpstreamClient(FakeUpstreamClient):
    def __init__(self) -> None:
        super().__init__()
        self.block_append_events = True

    async def send_json(self, event: dict[str, Any]) -> None:
        if event.get("type") == "input_audio_buffer.append":
            while self.block_append_events:
                await _sleep(0.001)
        await super().send_json(event)


class InFlightAppendBlockingUpstreamClient(FakeUpstreamClient):
    def __init__(self) -> None:
        super().__init__()
        self.append_started = asyncio.Event()
        self.allow_append_complete = asyncio.Event()

    async def send_json(self, event: dict[str, Any]) -> None:
        if event.get("type") == "input_audio_buffer.append":
            self.append_started.set()
            await self.allow_append_complete.wait()
        await super().send_json(event)


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

    async def _scenario() -> None:
        payload = b"\x01\x02\x03\x04"
        await bridge.append_client_audio(payload)
        await asyncio.wait_for(bridge._client_audio_queue.join(), timeout=0.5)
        await bridge.close()

    _run(_scenario())
    assert not envelopes
    assert not frames
    assert len(upstream.sent_events) == 1
    assert upstream.sent_events[0]["type"] == "input_audio_buffer.append"
    assert base64.b64decode(upstream.sent_events[0]["audio"]) == b"\x01\x02\x03\x04"


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


def test_duplicate_done_events_emit_stop_response_once() -> None:
    envelopes: list[tuple[str, dict[str, Any]]] = []
    upstream = FakeUpstreamClient()
    bridge = IOSRealtimeBridge(
        session_id="sess_test",
        upstream_client=upstream,
        send_envelope=lambda m_type, payload: _capture_envelope(
            envelopes, m_type, payload
        ),
        send_binary_frame=lambda *_args, **_kwargs: _noop_async(),
        manual_turn_fallback_enabled=False,
    )

    audio_bytes = b"\x10\x20\x30"
    audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
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
    _run(
        bridge._handle_upstream_event(
            {
                "type": "response.done",
                "response_id": "resp_1",
            }
        )
    )

    stop_payloads = [
        payload
        for message_type, payload in envelopes
        if message_type == "assistant.playback.control"
        and payload.get("command") == "stop_response"
    ]
    assert stop_payloads == [{"command": "stop_response", "response_id": "resp_1"}]


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


def test_upstream_error_event_parses_retriable_variants() -> None:
    test_cases = [
        (False, False),
        (True, True),
        (0, False),
        (1, True),
        ("false", False),
        ("0", False),
        ("no", False),
        ("true", True),
        ("1", True),
        ("yes", True),
        ("unexpected", True),
        (None, True),
    ]

    for raw_retriable, expected in test_cases:
        envelopes: list[tuple[str, dict[str, Any]]] = []
        upstream = FakeUpstreamClient()
        bridge = IOSRealtimeBridge(
            session_id="sess_test",
            upstream_client=upstream,
            send_envelope=lambda m_type, payload: _capture_envelope(
                envelopes, m_type, payload
            ),
            send_binary_frame=lambda *_args, **_kwargs: _noop_async(),
            manual_turn_fallback_enabled=False,
        )

        _run(
            bridge._handle_upstream_event(
                {
                    "type": "error",
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": "Too many requests",
                        "retriable": raw_retriable,
                    },
                }
            )
        )

        assert (
            "error",
            {
                "code": "RATE_LIMITED",
                "message": "Too many requests",
                "retriable": expected,
            },
        ) in envelopes


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

    async def _scenario() -> None:
        await bridge.append_client_audio(b"\x01\x02")
        await bridge._handle_upstream_event({"type": "input_audio_buffer.speech_stopped"})
        await bridge._handle_upstream_event({"type": "input_audio_buffer.speech_stopped"})
        await asyncio.wait_for(bridge._client_audio_queue.join(), timeout=0.5)
        await bridge.close()

    _run(_scenario())

    assert [event["type"] for event in upstream.sent_events] == [
        "input_audio_buffer.append",
        "input_audio_buffer.commit",
        "response.create",
    ]


def test_manual_finalize_waits_for_in_flight_append_before_commit() -> None:
    upstream = InFlightAppendBlockingUpstreamClient()
    bridge = IOSRealtimeBridge(
        session_id="sess_test",
        upstream_client=upstream,
        send_envelope=lambda *_args, **_kwargs: _noop_async(),
        send_binary_frame=lambda *_args, **_kwargs: _noop_async(),
        manual_turn_fallback_enabled=True,
        manual_turn_fallback_delay_ms=5_000,
    )

    async def _scenario() -> None:
        await bridge.append_client_audio(b"\x01\x02")
        await asyncio.wait_for(upstream.append_started.wait(), timeout=0.5)
        finalize_task = asyncio.create_task(
            bridge._handle_upstream_event({"type": "input_audio_buffer.speech_stopped"})
        )
        await _sleep(0.03)
        assert not any(
            event.get("type") in {"input_audio_buffer.commit", "response.create"}
            for event in upstream.sent_events
        )
        upstream.allow_append_complete.set()
        await asyncio.wait_for(finalize_task, timeout=0.5)
        await bridge.close()

    _run(_scenario())

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

    async def _scenario() -> None:
        await bridge.append_client_audio(b"\x01\x02")
        await _sleep(0.13)
        await bridge.append_client_audio(b"\x03\x04")
        await asyncio.wait_for(bridge._client_audio_queue.join(), timeout=0.5)
        await bridge.close()

    _run(_scenario())

    assert [event["type"] for event in upstream.sent_events] == [
        "input_audio_buffer.append",
        "input_audio_buffer.append",
        "input_audio_buffer.commit",
        "response.create",
    ]


def test_missing_response_id_does_not_reuse_previous_turn_response_id() -> None:
    envelopes: list[tuple[str, dict[str, Any]]] = []
    upstream = FakeUpstreamClient()
    bridge = IOSRealtimeBridge(
        session_id="sess_test",
        upstream_client=upstream,
        send_envelope=lambda m_type, payload: _capture_envelope(
            envelopes, m_type, payload
        ),
        send_binary_frame=lambda *_args, **_kwargs: _noop_async(),
        manual_turn_fallback_enabled=False,
    )

    audio_b64 = base64.b64encode(b"\x10\x20\x30").decode("ascii")
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
    _run(
        bridge._handle_upstream_event(
            {
                "type": "response.output_audio.delta",
                "delta": audio_b64,
            }
        )
    )

    start_ids = [
        payload["response_id"]
        for message_type, payload in envelopes
        if message_type == "assistant.playback.control"
        and payload.get("command") == "start_response"
    ]
    assert len(start_ids) == 2
    assert start_ids[0] == "resp_1"
    assert start_ids[1] != "resp_1"


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

    async def _scenario() -> None:
        await bridge.append_client_audio(b"\x01\x02")
        await bridge._handle_upstream_event({"type": "response.created"})
        await bridge._handle_upstream_event({"type": "input_audio_buffer.speech_stopped"})
        await asyncio.wait_for(bridge._client_audio_queue.join(), timeout=0.5)
        await bridge.close()

    _run(_scenario())

    assert [event["type"] for event in upstream.sent_events] == [
        "input_audio_buffer.append",
    ]


def test_connect_and_start_waits_for_upstream_session_ready() -> None:
    upstream = EventfulFakeUpstreamClient(
        [{"type": "session.created"}, {"type": "session.updated"}]
    )
    bridge = IOSRealtimeBridge(
        session_id="sess_test",
        upstream_client=upstream,
        send_envelope=lambda *_args, **_kwargs: _noop_async(),
        send_binary_frame=lambda *_args, **_kwargs: _noop_async(),
        manual_turn_fallback_enabled=False,
    )

    _run(bridge.connect_and_start())

    assert upstream.connected is True
    assert upstream.initialized is True


def test_connect_and_start_fails_when_upstream_errors_before_ready() -> None:
    upstream = EventfulFakeUpstreamClient(
        [
            {
                "type": "error",
                "error": {
                    "code": "invalid_request_error",
                    "message": "Invalid session config",
                    "retriable": False,
                },
            }
        ]
    )
    envelopes: list[tuple[str, dict[str, Any]]] = []
    bridge = IOSRealtimeBridge(
        session_id="sess_test",
        upstream_client=upstream,
        send_envelope=lambda m_type, payload: _capture_envelope(
            envelopes, m_type, payload
        ),
        send_binary_frame=lambda *_args, **_kwargs: _noop_async(),
        manual_turn_fallback_enabled=False,
    )

    try:
        _run(bridge.connect_and_start())
        raised_error: Exception | None = None
    except Exception as exc:
        raised_error = exc

    assert isinstance(raised_error, RealtimeClientError)
    assert "invalid_request_error" in str(raised_error)
    assert (
        "error",
        {
            "code": "invalid_request_error",
            "message": "Invalid session config",
            "retriable": False,
        },
    ) in envelopes


def test_client_audio_queue_overflow_drops_oldest_payload() -> None:
    upstream = BlockingAppendFakeUpstreamClient()
    bridge = IOSRealtimeBridge(
        session_id="sess_test",
        upstream_client=upstream,
        send_envelope=lambda *_args, **_kwargs: _noop_async(),
        send_binary_frame=lambda *_args, **_kwargs: _noop_async(),
        client_audio_queue_maxsize=2,
        manual_turn_fallback_enabled=False,
    )

    async def _scenario() -> None:
        await bridge.append_client_audio(b"\x01")
        await _sleep(0)
        await bridge.append_client_audio(b"\x02")
        await bridge.append_client_audio(b"\x03")
        await bridge.append_client_audio(b"\x04")
        upstream.block_append_events = False
        await asyncio.wait_for(bridge._client_audio_queue.join(), timeout=0.5)
        await bridge.close()

    _run(_scenario())

    append_payloads = [
        base64.b64decode(event["audio"])
        for event in upstream.sent_events
        if event.get("type") == "input_audio_buffer.append"
    ]
    assert append_payloads == [b"\x01", b"\x03", b"\x04"]
    assert bridge._client_audio_dropped_oldest_count == 1


def test_close_shuts_down_client_audio_sender_task() -> None:
    upstream = BlockingAppendFakeUpstreamClient()
    bridge = IOSRealtimeBridge(
        session_id="sess_test",
        upstream_client=upstream,
        send_envelope=lambda *_args, **_kwargs: _noop_async(),
        send_binary_frame=lambda *_args, **_kwargs: _noop_async(),
        manual_turn_fallback_enabled=False,
    )

    async def _scenario() -> None:
        await bridge.append_client_audio(b"\x09")
        await _sleep(0)
        await asyncio.wait_for(bridge.close(), timeout=1.2)

    _run(_scenario())

    assert upstream.closed is True
    assert bridge._client_audio_sender_task is None


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
