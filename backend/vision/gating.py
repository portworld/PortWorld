from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

from backend.vision.contracts import VisionFrameContext


@dataclass(frozen=True, slots=True)
class VisionProviderBudgetState:
    available_now: bool
    available_at_ms: int
    cooldown_until_ms: int | None
    consecutive_rate_limit_count: int
    reason: str


class VisionGateError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AcceptedFrameReference:
    capture_ts_ms: int
    dhash_hex: str


@dataclass(frozen=True, slots=True)
class VisionSignalSnapshot:
    session_id: str
    frame_id: str
    capture_ts_ms: int
    is_first_frame: bool
    capture_gap_ms: int | None
    dhash_hex: str
    hamming_distance: int | None
    has_short_term_memory: bool
    has_session_memory: bool
    short_term_memory_age_ms: int | None
    session_memory_age_ms: int | None
    last_successful_analysis_at_ms: int | None
    last_analysis_failed: bool
    provider_available_now: bool
    provider_cooldown_until_ms: int | None
    provider_budget_reason: str


@dataclass(frozen=True, slots=True)
class VisionRouteDecision:
    session_id: str
    frame_id: str
    action: str
    reason: str
    priority_score: float
    novelty_score: float
    freshness_score: float
    memory_bootstrap_required: bool
    provider_budget_available: bool
    provider_cooldown_until_ms: int | None


def extract_vision_signal_snapshot(
    *,
    image_bytes: bytes,
    frame_context: VisionFrameContext,
    last_accepted_frame: AcceptedFrameReference | None,
    has_short_term_memory: bool,
    has_session_memory: bool,
    short_term_memory_age_ms: int | None,
    session_memory_age_ms: int | None,
    last_successful_analysis_at_ms: int | None,
    last_analysis_failed: bool,
    provider_budget_state: VisionProviderBudgetState,
) -> VisionSignalSnapshot:
    dhash_hex = compute_dhash_hex(image_bytes)
    if last_accepted_frame is None:
        return VisionSignalSnapshot(
            session_id=frame_context.session_id,
            frame_id=frame_context.frame_id,
            capture_ts_ms=frame_context.capture_ts_ms,
            is_first_frame=True,
            capture_gap_ms=None,
            dhash_hex=dhash_hex,
            hamming_distance=None,
            has_short_term_memory=has_short_term_memory,
            has_session_memory=has_session_memory,
            short_term_memory_age_ms=short_term_memory_age_ms,
            session_memory_age_ms=session_memory_age_ms,
            last_successful_analysis_at_ms=last_successful_analysis_at_ms,
            last_analysis_failed=last_analysis_failed,
            provider_available_now=provider_budget_state.available_now,
            provider_cooldown_until_ms=provider_budget_state.cooldown_until_ms,
            provider_budget_reason=provider_budget_state.reason,
        )

    capture_gap_ms = frame_context.capture_ts_ms - last_accepted_frame.capture_ts_ms
    hamming_distance = hamming_distance_hex(dhash_hex, last_accepted_frame.dhash_hex)
    return VisionSignalSnapshot(
        session_id=frame_context.session_id,
        frame_id=frame_context.frame_id,
        capture_ts_ms=frame_context.capture_ts_ms,
        is_first_frame=False,
        capture_gap_ms=capture_gap_ms,
        dhash_hex=dhash_hex,
        hamming_distance=hamming_distance,
        has_short_term_memory=has_short_term_memory,
        has_session_memory=has_session_memory,
        short_term_memory_age_ms=short_term_memory_age_ms,
        session_memory_age_ms=session_memory_age_ms,
        last_successful_analysis_at_ms=last_successful_analysis_at_ms,
        last_analysis_failed=last_analysis_failed,
        provider_available_now=provider_budget_state.available_now,
        provider_cooldown_until_ms=provider_budget_state.cooldown_until_ms,
        provider_budget_reason=provider_budget_state.reason,
    )


def decide_vision_route(
    *,
    signal: VisionSignalSnapshot,
    min_analysis_gap_seconds: int,
    scene_change_hamming_threshold: int,
    analysis_heartbeat_seconds: int,
) -> VisionRouteDecision:
    min_gap_ms = max(1, min_analysis_gap_seconds) * 1000
    heartbeat_ms = max(1, analysis_heartbeat_seconds) * 1000
    memory_bootstrap_required = (
        signal.is_first_frame
        or signal.last_successful_analysis_at_ms is None
        or not signal.has_short_term_memory
        or not signal.has_session_memory
    )
    within_gap = signal.capture_gap_ms is not None and signal.capture_gap_ms < min_gap_ms
    is_novel_scene = (
        signal.hamming_distance is None
        or signal.hamming_distance >= scene_change_hamming_threshold
    )
    known_memory_ages = [
        age
        for age in (signal.short_term_memory_age_ms, signal.session_memory_age_ms)
        if age is not None
    ]
    freshest_memory_age_ms = min(known_memory_ages) if known_memory_ages else None
    heartbeat_refresh_due = (
        freshest_memory_age_ms is not None and freshest_memory_age_ms >= heartbeat_ms
    )
    novelty_score = (
        1.0
        if signal.hamming_distance is None
        else min(1.0, signal.hamming_distance / max(1, scene_change_hamming_threshold))
    )
    if memory_bootstrap_required or heartbeat_refresh_due:
        freshness_score = 1.0
    elif freshest_memory_age_ms is None:
        freshness_score = 0.0
    else:
        freshness_score = min(1.0, freshest_memory_age_ms / heartbeat_ms)
    priority_score = round(
        (0.65 * novelty_score)
        + (0.35 * freshness_score)
        + (0.5 if memory_bootstrap_required else 0.0),
        6,
    )

    if not memory_bootstrap_required and within_gap and not is_novel_scene and not heartbeat_refresh_due:
        return VisionRouteDecision(
            session_id=signal.session_id,
            frame_id=signal.frame_id,
            action="drop_redundant",
            reason="too_similar_within_gap",
            priority_score=priority_score,
            novelty_score=novelty_score,
            freshness_score=freshness_score,
            memory_bootstrap_required=memory_bootstrap_required,
            provider_budget_available=signal.provider_available_now,
            provider_cooldown_until_ms=signal.provider_cooldown_until_ms,
        )

    heavy_analysis_worthy = memory_bootstrap_required or is_novel_scene or heartbeat_refresh_due
    if heavy_analysis_worthy:
        if signal.provider_available_now:
            if memory_bootstrap_required:
                reason = "first_frame_bootstrap" if signal.is_first_frame else "missing_memory_bootstrap"
            elif is_novel_scene:
                reason = "novel_scene"
            else:
                reason = "memory_heartbeat_refresh"
            return VisionRouteDecision(
                session_id=signal.session_id,
                frame_id=signal.frame_id,
                action="analyze_now",
                reason=reason,
                priority_score=priority_score,
                novelty_score=novelty_score,
                freshness_score=freshness_score,
                memory_bootstrap_required=memory_bootstrap_required,
                provider_budget_available=signal.provider_available_now,
                provider_cooldown_until_ms=signal.provider_cooldown_until_ms,
            )
        return VisionRouteDecision(
            session_id=signal.session_id,
            frame_id=signal.frame_id,
            action="defer_candidate",
            reason="provider_budget_unavailable",
            priority_score=priority_score,
            novelty_score=novelty_score,
            freshness_score=freshness_score,
            memory_bootstrap_required=memory_bootstrap_required,
            provider_budget_available=signal.provider_available_now,
            provider_cooldown_until_ms=signal.provider_cooldown_until_ms,
        )

    return VisionRouteDecision(
        session_id=signal.session_id,
        frame_id=signal.frame_id,
        action="store_only",
        reason="no_heavy_analysis_needed",
        priority_score=priority_score,
        novelty_score=novelty_score,
        freshness_score=freshness_score,
        memory_bootstrap_required=memory_bootstrap_required,
        provider_budget_available=signal.provider_available_now,
        provider_cooldown_until_ms=signal.provider_cooldown_until_ms,
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
