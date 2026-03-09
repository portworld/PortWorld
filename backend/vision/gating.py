from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

from backend.vision.contracts import VisionFrameContext


@dataclass(frozen=True, slots=True)
class GateDecision:
    accepted: bool
    reason: str
    dhash_hex: str
    hamming_distance: int | None


class VisionGateError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AcceptedFrameReference:
    capture_ts_ms: int
    dhash_hex: str


def evaluate_frame_gate(
    *,
    image_bytes: bytes,
    frame_context: VisionFrameContext,
    last_accepted_frame: AcceptedFrameReference | None,
    min_analysis_gap_seconds: int,
    scene_change_hamming_threshold: int,
) -> GateDecision:
    dhash_hex = compute_dhash_hex(image_bytes)
    if last_accepted_frame is None:
        return GateDecision(
            accepted=True,
            reason="first_frame",
            dhash_hex=dhash_hex,
            hamming_distance=None,
        )

    capture_gap_ms = frame_context.capture_ts_ms - last_accepted_frame.capture_ts_ms
    hamming_distance = hamming_distance_hex(dhash_hex, last_accepted_frame.dhash_hex)
    if (
        capture_gap_ms < max(1, min_analysis_gap_seconds) * 1000
        and hamming_distance < scene_change_hamming_threshold
    ):
        return GateDecision(
            accepted=False,
            reason="too_similar_within_gap",
            dhash_hex=dhash_hex,
            hamming_distance=hamming_distance,
        )
    if capture_gap_ms < max(1, min_analysis_gap_seconds) * 1000:
        return GateDecision(
            accepted=True,
            reason="scene_changed_within_gap",
            dhash_hex=dhash_hex,
            hamming_distance=hamming_distance,
        )
    return GateDecision(
        accepted=True,
        reason="min_gap_elapsed",
        dhash_hex=dhash_hex,
        hamming_distance=hamming_distance,
    )


def compute_dhash_hex(image_bytes: bytes) -> str:
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            grayscale = image.convert("L")
            resized = grayscale.resize((9, 8), Image.Resampling.LANCZOS)
    except (UnidentifiedImageError, OSError) as exc:
        raise VisionGateError("Unable to decode image bytes for gating") from exc

    bits = 0
    for row in range(8):
        for column in range(8):
            left = resized.getpixel((column, row))
            right = resized.getpixel((column + 1, row))
            bits = (bits << 1) | int(left > right)
    return f"{bits:016x}"


def hamming_distance_hex(lhs: str, rhs: str) -> int:
    return (int(lhs, 16) ^ int(rhs, 16)).bit_count()
