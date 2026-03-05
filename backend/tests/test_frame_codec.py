from __future__ import annotations

import pytest

from backend.frame_codec import (
    CLIENT_AUDIO_FRAME_TYPE,
    SERVER_AUDIO_FRAME_TYPE,
    ShortFrameError,
    TimestampRangeError,
    UnsupportedFrameTypeError,
    decode_frame,
    encode_frame,
)


def test_encode_decode_roundtrip_client_audio_frame() -> None:
    payload = b"\x01\x02\x03\x04"
    raw = encode_frame(CLIENT_AUDIO_FRAME_TYPE, 1_740_777_601_000, payload)

    frame_type, ts_ms, decoded_payload = decode_frame(raw)

    assert frame_type == CLIENT_AUDIO_FRAME_TYPE
    assert ts_ms == 1_740_777_601_000
    assert decoded_payload == payload


def test_encode_decode_roundtrip_server_audio_frame_with_negative_timestamp() -> None:
    payload = b"pcm16-audio"
    raw = encode_frame(SERVER_AUDIO_FRAME_TYPE, -123, payload)

    frame_type, ts_ms, decoded_payload = decode_frame(raw)

    assert frame_type == SERVER_AUDIO_FRAME_TYPE
    assert ts_ms == -123
    assert decoded_payload == payload


def test_decode_frame_rejects_too_short_frame() -> None:
    with pytest.raises(ShortFrameError):
        decode_frame(b"\x01\x02\x03")


def test_encode_decode_reject_unsupported_frame_type() -> None:
    with pytest.raises(UnsupportedFrameTypeError):
        encode_frame(0x7F, 10, b"x")

    with pytest.raises(UnsupportedFrameTypeError):
        decode_frame(bytes([0x7F]) + (0).to_bytes(8, "little") + b"x")


def test_encode_frame_rejects_out_of_range_timestamp() -> None:
    with pytest.raises(TimestampRangeError):
        encode_frame(CLIENT_AUDIO_FRAME_TYPE, (1 << 64) + 1, b"x")
