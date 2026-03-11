from __future__ import annotations

HEADER_SIZE = 9

CLIENT_AUDIO_FRAME_TYPE = 0x01
SERVER_AUDIO_FRAME_TYPE = 0x02
CLIENT_PROBE_FRAME_TYPE = 0x03
SUPPORTED_FRAME_TYPES = {
    CLIENT_AUDIO_FRAME_TYPE,
    SERVER_AUDIO_FRAME_TYPE,
    CLIENT_PROBE_FRAME_TYPE,
}

_INT64_MIN = -(1 << 63)
_INT64_MAX = (1 << 63) - 1
_UINT64_MAX = (1 << 64) - 1
_INT64_MODULUS = 1 << 64


class FrameCodecError(ValueError):
    """Base error type for frame codec failures."""


class ShortFrameError(FrameCodecError):
    """Raised when a binary frame is shorter than the required header."""


class UnsupportedFrameTypeError(FrameCodecError):
    """Raised when the frame type is unknown."""


class TimestampRangeError(FrameCodecError):
    """Raised when timestamp cannot fit in a 64-bit bit pattern."""

def _validate_frame_type(frame_type: int) -> None:
    if frame_type not in SUPPORTED_FRAME_TYPES:
        raise UnsupportedFrameTypeError(
            f"Unsupported frame type: {frame_type:#04x}. Supported: 0x01, 0x02, 0x03."
        )


def _int_to_uint64_bit_pattern(value: int) -> int:
    """Return a UInt64 bit pattern for an Int64-compatible integer."""

    if _INT64_MIN <= value <= _INT64_MAX:
        return value & _UINT64_MAX
    if 0 <= value <= _UINT64_MAX:
        return value
    raise TimestampRangeError(
        f"Timestamp {value} is outside Int64/UInt64 range and cannot be encoded."
    )


def _uint64_bit_pattern_to_int64(value: int) -> int:
    """Interpret UInt64 bit pattern as signed Int64 (Swift-compatible)."""

    if value >= (1 << 63):
        return value - _INT64_MODULUS
    return value


def encode_frame(frame_type: int, ts_ms: int, payload_bytes: bytes) -> bytes:
    """Encode a transport frame as: 1-byte type + 8-byte LE timestamp + payload."""

    _validate_frame_type(frame_type)
    if not isinstance(payload_bytes, (bytes, bytearray, memoryview)):
        raise TypeError("payload_bytes must be bytes-like.")

    ts_bits = _int_to_uint64_bit_pattern(ts_ms)
    header = bytes([frame_type]) + ts_bits.to_bytes(8, byteorder="little", signed=False)
    return header + bytes(payload_bytes)


def decode_frame(raw_bytes: bytes) -> tuple[int, int, bytes]:
    """Decode raw frame bytes into (frame_type, ts_ms, payload_bytes)."""

    if not isinstance(raw_bytes, (bytes, bytearray, memoryview)):
        raise TypeError("raw_bytes must be bytes-like.")

    raw = bytes(raw_bytes)
    if len(raw) < HEADER_SIZE:
        raise ShortFrameError(
            f"Frame too short: expected at least {HEADER_SIZE} bytes, got {len(raw)}."
        )

    frame_type = raw[0]
    _validate_frame_type(frame_type)

    ts_bits = int.from_bytes(raw[1:9], byteorder="little", signed=False)
    ts_ms = _uint64_bit_pattern_to_int64(ts_bits)
    payload = raw[9:]
    return frame_type, ts_ms, payload
