from __future__ import annotations

from typing import Any

EXPECTED_CLIENT_AUDIO_ENCODING = "pcm_s16le"
EXPECTED_CLIENT_AUDIO_CHANNELS = 1
EXPECTED_CLIENT_AUDIO_SAMPLE_RATE = 24_000


def as_integral_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def validate_client_audio_format_payload(
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    raw_format = payload.get("client_audio_format")
    if raw_format is None:
        raw_format = payload.get("audio_format")
    if raw_format is None:
        return None

    if not isinstance(raw_format, dict):
        return {
            "code": "INVALID_CLIENT_AUDIO_FORMAT",
            "message": "client_audio_format must be an object",
            "retriable": False,
        }

    encoding = raw_format.get("encoding")
    channels = as_integral_int(raw_format.get("channels"))
    sample_rate = as_integral_int(raw_format.get("sample_rate"))

    if not isinstance(encoding, str) or channels is None or sample_rate is None:
        return {
            "code": "INVALID_CLIENT_AUDIO_FORMAT",
            "message": (
                "client_audio_format requires encoding (string), channels (int), "
                "and sample_rate (int)"
            ),
            "retriable": False,
        }

    normalized_encoding = encoding.strip().lower()
    if (
        normalized_encoding != EXPECTED_CLIENT_AUDIO_ENCODING
        or channels != EXPECTED_CLIENT_AUDIO_CHANNELS
        or sample_rate != EXPECTED_CLIENT_AUDIO_SAMPLE_RATE
    ):
        return {
            "code": "UNSUPPORTED_CLIENT_AUDIO_FORMAT",
            "message": (
                "Unsupported client audio format. Expected "
                f"{EXPECTED_CLIENT_AUDIO_ENCODING}/{EXPECTED_CLIENT_AUDIO_CHANNELS}ch/"
                f"{EXPECTED_CLIENT_AUDIO_SAMPLE_RATE}Hz."
            ),
            "retriable": False,
        }

    return None
