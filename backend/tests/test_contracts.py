from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.contracts import IOSEnvelope, make_envelope


def test_make_envelope_has_expected_shape_and_required_fields() -> None:
    envelope = make_envelope(
        message_type="health.pong",
        session_id="sess_123",
        seq=7,
        payload={"ok": True},
    )

    data = envelope.model_dump()

    assert set(data.keys()) == {"type", "session_id", "seq", "ts_ms", "payload"}
    assert data["type"] == "health.pong"
    assert data["session_id"] == "sess_123"
    assert data["seq"] == 7
    assert isinstance(data["ts_ms"], int)
    assert data["payload"] == {"ok": True}


@pytest.mark.parametrize(
    "payload",
    [
        {"session_id": "sess", "seq": 1, "ts_ms": 1, "payload": {}},
        {"type": "session.state", "seq": 1, "ts_ms": 1, "payload": {}},
        {"type": "session.state", "session_id": "sess", "ts_ms": 1, "payload": {}},
        {"type": "session.state", "session_id": "sess", "seq": 1, "payload": {}},
    ],
)
def test_ios_envelope_required_fields_are_enforced(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        IOSEnvelope.model_validate(payload)


def test_make_envelope_defaults_payload_to_empty_dict() -> None:
    envelope = make_envelope(
        message_type="session.state",
        session_id="sess_abc",
        seq=1,
        payload=None,
    )

    assert envelope.payload == {}
