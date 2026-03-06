from __future__ import annotations

import base64
import json
import time
from dataclasses import replace
from typing import Any

from fastapi.testclient import TestClient

from backend.app import app
from backend.frame_codec import (
    CLIENT_AUDIO_FRAME_TYPE,
    CLIENT_PROBE_FRAME_TYPE,
    encode_frame,
)
from backend.routers import ws as ws_router


class DummyRealtimeClient:
    def __init__(self, **_kwargs: Any) -> None:
        pass


class FakeBridge:
    created: list["FakeBridge"] = []

    def __init__(self, **_kwargs: Any) -> None:
        self.appended_audio: list[bytes] = []
        self.closed = False
        self.connected = False
        FakeBridge.created.append(self)

    async def connect_and_start(self) -> None:
        self.connected = True

    async def append_client_audio(self, payload_bytes: bytes) -> None:
        self.appended_audio.append(payload_bytes)

    async def close(self) -> None:
        self.closed = True


class FailingBridge(FakeBridge):
    async def append_client_audio(self, payload_bytes: bytes) -> None:
        self.appended_audio.append(payload_bytes)
        raise ws_router.RealtimeClientError("boom")


def _make_envelope(message_type: str, session_id: str, payload: dict[str, Any]) -> str:
    return json.dumps(
        {
            "type": message_type,
            "session_id": session_id,
            "seq": 0,
            "ts_ms": 1_742_000_000_000,
            "payload": payload,
        }
    )


def _wait_for_audio(bridge: FakeBridge, expected_payload: bytes) -> None:
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if bridge.appended_audio == [expected_payload]:
            return
        time.sleep(0.01)
    assert bridge.appended_audio == [expected_payload]


def test_ws_session_routes_binary_audio_frames_to_bridge(monkeypatch) -> None:
    FakeBridge.created = []
    monkeypatch.setattr(ws_router, "OpenAIRealtimeClient", DummyRealtimeClient)
    monkeypatch.setattr(ws_router, "IOSRealtimeBridge", FakeBridge)
    monkeypatch.setattr(
        ws_router,
        "settings",
        replace(
            ws_router.settings,
            openai_api_key="test-key",
            openai_realtime_allow_text_audio_fallback=False,
        ),
    )

    client = TestClient(app)
    payload = b"\x01\x02\x03\x04"
    activate = _make_envelope(
        "session.activate",
        "sess_binary",
        {
            "session": {"type": "realtime"},
            "audio_format": {
                "encoding": "pcm_s16le",
                "channels": 1,
                "sample_rate": 24_000,
            },
        },
    )

    with client.websocket_connect("/ws/session") as websocket:
        websocket.send_text(activate)
        response = websocket.receive_json()
        assert response["type"] == "session.state"
        assert response["payload"]["state"] == "active"

        websocket.send_bytes(encode_frame(CLIENT_AUDIO_FRAME_TYPE, 42, payload))
        ack = websocket.receive_json()
        assert ack["type"] == "transport.uplink.ack"
        assert ack["payload"] == {
            "frames_received": 1,
            "bytes_received": len(payload),
            "probe_acknowledged": False,
        }

        assert len(FakeBridge.created) == 1
        assert FakeBridge.created[0].connected is True
        _wait_for_audio(FakeBridge.created[0], payload)


def test_ws_session_rejects_text_audio_fallback_when_disabled(monkeypatch) -> None:
    FakeBridge.created = []
    monkeypatch.setattr(ws_router, "OpenAIRealtimeClient", DummyRealtimeClient)
    monkeypatch.setattr(ws_router, "IOSRealtimeBridge", FakeBridge)
    monkeypatch.setattr(
        ws_router,
        "settings",
        replace(
            ws_router.settings,
            openai_api_key="test-key",
            openai_realtime_allow_text_audio_fallback=False,
        ),
    )

    client = TestClient(app)
    activate = _make_envelope(
        "session.activate",
        "sess_text_disabled",
        {
            "session": {"type": "realtime"},
            "audio_format": {
                "encoding": "pcm_s16le",
                "channels": 1,
                "sample_rate": 24_000,
            },
        },
    )
    client_audio = _make_envelope(
        "client.audio",
        "sess_text_disabled",
        {"audio_b64": base64.b64encode(b"\x05\x06").decode("ascii")},
    )

    with client.websocket_connect("/ws/session") as websocket:
        websocket.send_text(activate)
        websocket.receive_json()

        websocket.send_text(client_audio)
        response = websocket.receive_json()
        assert response["type"] == "error"
        assert response["payload"]["code"] == "TEXT_AUDIO_FALLBACK_DISABLED"

        assert len(FakeBridge.created) == 1
        assert FakeBridge.created[0].appended_audio == []


def test_ws_session_routes_text_audio_fallback_when_enabled(monkeypatch) -> None:
    FakeBridge.created = []
    monkeypatch.setattr(ws_router, "OpenAIRealtimeClient", DummyRealtimeClient)
    monkeypatch.setattr(ws_router, "IOSRealtimeBridge", FakeBridge)
    monkeypatch.setattr(
        ws_router,
        "settings",
        replace(
            ws_router.settings,
            openai_api_key="test-key",
            openai_realtime_allow_text_audio_fallback=True,
        ),
    )

    client = TestClient(app)
    payload = b"\x07\x08\x09"
    activate = _make_envelope(
        "session.activate",
        "sess_text_enabled",
        {
            "session": {"type": "realtime"},
            "audio_format": {
                "encoding": "pcm_s16le",
                "channels": 1,
                "sample_rate": 24_000,
            },
        },
    )
    client_audio = _make_envelope(
        "client.audio",
        "sess_text_enabled",
        {"audio_b64": base64.b64encode(payload).decode("ascii")},
    )

    with client.websocket_connect("/ws/session") as websocket:
        websocket.send_text(activate)
        websocket.receive_json()

        websocket.send_text(client_audio)
        ack = websocket.receive_json()
        assert ack["type"] == "transport.uplink.ack"
        assert ack["payload"] == {
            "frames_received": 1,
            "bytes_received": len(payload),
            "probe_acknowledged": False,
        }

        assert len(FakeBridge.created) == 1
        _wait_for_audio(FakeBridge.created[0], payload)


def test_ws_session_acks_probe_frame_without_forwarding_upstream(monkeypatch) -> None:
    FakeBridge.created = []
    monkeypatch.setattr(ws_router, "OpenAIRealtimeClient", DummyRealtimeClient)
    monkeypatch.setattr(ws_router, "IOSRealtimeBridge", FakeBridge)
    monkeypatch.setattr(
        ws_router,
        "settings",
        replace(
            ws_router.settings,
            openai_api_key="test-key",
            openai_realtime_allow_text_audio_fallback=False,
        ),
    )

    client = TestClient(app)
    activate = _make_envelope(
        "session.activate",
        "sess_probe",
        {
            "session": {"type": "realtime"},
            "audio_format": {
                "encoding": "pcm_s16le",
                "channels": 1,
                "sample_rate": 24_000,
            },
        },
    )

    with client.websocket_connect("/ws/session") as websocket:
        websocket.send_text(activate)
        websocket.receive_json()

        websocket.send_bytes(encode_frame(CLIENT_PROBE_FRAME_TYPE, 42, b"PWP1"))
        ack = websocket.receive_json()
        assert ack["type"] == "transport.uplink.ack"
        assert ack["payload"] == {
            "frames_received": 0,
            "bytes_received": 0,
            "probe_acknowledged": True,
        }
        assert len(FakeBridge.created) == 1
        assert FakeBridge.created[0].appended_audio == []


def test_ws_session_routes_audio_after_probe_on_same_connection(monkeypatch) -> None:
    FakeBridge.created = []
    monkeypatch.setattr(ws_router, "OpenAIRealtimeClient", DummyRealtimeClient)
    monkeypatch.setattr(ws_router, "IOSRealtimeBridge", FakeBridge)
    monkeypatch.setattr(
        ws_router,
        "settings",
        replace(
            ws_router.settings,
            openai_api_key="test-key",
            openai_realtime_allow_text_audio_fallback=False,
        ),
    )

    client = TestClient(app)
    payload = b"\x01\x02\x03\x04"
    activate = _make_envelope(
        "session.activate",
        "sess_probe_then_audio",
        {
            "session": {"type": "realtime"},
            "audio_format": {
                "encoding": "pcm_s16le",
                "channels": 1,
                "sample_rate": 24_000,
            },
        },
    )

    with client.websocket_connect("/ws/session") as websocket:
        websocket.send_text(activate)
        websocket.receive_json()

        websocket.send_bytes(encode_frame(CLIENT_PROBE_FRAME_TYPE, 42, b"PWP1"))
        probe_ack = websocket.receive_json()
        assert probe_ack["type"] == "transport.uplink.ack"
        assert probe_ack["payload"]["probe_acknowledged"] is True

        websocket.send_bytes(encode_frame(CLIENT_AUDIO_FRAME_TYPE, 43, payload))
        audio_ack = websocket.receive_json()
        assert audio_ack["type"] == "transport.uplink.ack"
        assert audio_ack["payload"] == {
            "frames_received": 1,
            "bytes_received": len(payload),
            "probe_acknowledged": False,
        }

        assert len(FakeBridge.created) == 1
        _wait_for_audio(FakeBridge.created[0], payload)


def test_ws_session_uses_configured_uplink_ack_cadence(monkeypatch) -> None:
    FakeBridge.created = []
    monkeypatch.setattr(ws_router, "OpenAIRealtimeClient", DummyRealtimeClient)
    monkeypatch.setattr(ws_router, "IOSRealtimeBridge", FakeBridge)
    monkeypatch.setattr(
        ws_router,
        "settings",
        replace(
            ws_router.settings,
            openai_api_key="test-key",
            openai_realtime_allow_text_audio_fallback=False,
            openai_realtime_uplink_ack_every_n_frames=2,
        ),
    )

    client = TestClient(app)
    payload_1 = b"\x01\x02"
    payload_2 = b"\x03\x04\x05"
    activate = _make_envelope(
        "session.activate",
        "sess_ack_cadence",
        {
            "session": {"type": "realtime"},
            "audio_format": {
                "encoding": "pcm_s16le",
                "channels": 1,
                "sample_rate": 24_000,
            },
        },
    )

    with client.websocket_connect("/ws/session") as websocket:
        websocket.send_text(activate)
        websocket.receive_json()

        websocket.send_bytes(encode_frame(CLIENT_AUDIO_FRAME_TYPE, 41, payload_1))
        ack_1 = websocket.receive_json()
        assert ack_1["type"] == "transport.uplink.ack"
        assert ack_1["payload"] == {
            "frames_received": 1,
            "bytes_received": len(payload_1),
            "probe_acknowledged": False,
        }

        websocket.send_bytes(encode_frame(CLIENT_AUDIO_FRAME_TYPE, 42, payload_2))
        ack_2 = websocket.receive_json()
        assert ack_2["type"] == "transport.uplink.ack"
        assert ack_2["payload"] == {
            "frames_received": 2,
            "bytes_received": len(payload_1) + len(payload_2),
            "probe_acknowledged": False,
        }


def test_ws_session_reactivate_deactivates_and_unregisters_prior_session(monkeypatch) -> None:
    FakeBridge.created = []
    monkeypatch.setattr(ws_router, "OpenAIRealtimeClient", DummyRealtimeClient)
    monkeypatch.setattr(ws_router, "IOSRealtimeBridge", FakeBridge)
    monkeypatch.setattr(
        ws_router,
        "settings",
        replace(
            ws_router.settings,
            openai_api_key="test-key",
            openai_realtime_allow_text_audio_fallback=False,
        ),
    )

    client = TestClient(app)
    activate_1 = _make_envelope(
        "session.activate",
        "sess_reactivate_1",
        {
            "session": {"type": "realtime"},
            "audio_format": {
                "encoding": "pcm_s16le",
                "channels": 1,
                "sample_rate": 24_000,
            },
        },
    )
    activate_2 = _make_envelope(
        "session.activate",
        "sess_reactivate_2",
        {
            "session": {"type": "realtime"},
            "audio_format": {
                "encoding": "pcm_s16le",
                "channels": 1,
                "sample_rate": 24_000,
            },
        },
    )

    with client.websocket_connect("/ws/session") as websocket:
        websocket.send_text(activate_1)
        first_active = websocket.receive_json()
        assert first_active["type"] == "session.state"
        assert first_active["payload"]["state"] == "active"
        assert ws_router.session_registry.get("sess_reactivate_1") is not None
        assert FakeBridge.created[0].closed is False

        websocket.send_text(activate_2)
        first_ended = websocket.receive_json()
        assert first_ended["type"] == "session.state"
        assert first_ended["session_id"] == "sess_reactivate_1"
        assert first_ended["payload"]["state"] == "ended"

        second_active = websocket.receive_json()
        assert second_active["type"] == "session.state"
        assert second_active["session_id"] == "sess_reactivate_2"
        assert second_active["payload"]["state"] == "active"

        assert FakeBridge.created[0].closed is True
        assert ws_router.session_registry.get("sess_reactivate_1") is None
        assert ws_router.session_registry.get("sess_reactivate_2") is not None

    assert len(FakeBridge.created) == 2
    assert FakeBridge.created[1].closed is True
    assert ws_router.session_registry.get("sess_reactivate_2") is None


def test_ws_session_acks_binary_audio_before_upstream_forward_failure(monkeypatch) -> None:
    FakeBridge.created = []
    monkeypatch.setattr(ws_router, "OpenAIRealtimeClient", DummyRealtimeClient)
    monkeypatch.setattr(ws_router, "IOSRealtimeBridge", FailingBridge)
    monkeypatch.setattr(
        ws_router,
        "settings",
        replace(
            ws_router.settings,
            openai_api_key="test-key",
            openai_realtime_allow_text_audio_fallback=False,
        ),
    )

    client = TestClient(app)
    payload = b"\x01\x02\x03\x04"
    activate = _make_envelope(
        "session.activate",
        "sess_forward_fail",
        {
            "session": {"type": "realtime"},
            "audio_format": {
                "encoding": "pcm_s16le",
                "channels": 1,
                "sample_rate": 24_000,
            },
        },
    )

    with client.websocket_connect("/ws/session") as websocket:
        websocket.send_text(activate)
        websocket.receive_json()

        websocket.send_bytes(encode_frame(CLIENT_AUDIO_FRAME_TYPE, 42, payload))
        ack = websocket.receive_json()
        assert ack["type"] == "transport.uplink.ack"
        assert ack["payload"] == {
            "frames_received": 1,
            "bytes_received": len(payload),
            "probe_acknowledged": False,
        }

        error = websocket.receive_json()
        assert error["type"] == "error"
        assert error["payload"]["code"] == "UPSTREAM_SEND_FAILED"
