from __future__ import annotations

from collections.abc import Mapping
from typing import TypedDict

from backend.memory.normalize import coerce_optional_int, normalize_string, normalize_string_list


class AcceptedVisionEvent(TypedDict):
    event_type: str
    frame_id: str
    session_id: str
    capture_ts_ms: int
    analyzed_at_ms: int | None
    provider: str
    model: str
    scene_summary: str
    user_activity_guess: str
    entities: list[str]
    actions: list[str]
    visible_text: list[str]
    documents_seen: list[str]
    salient_change: bool
    confidence: float


def coerce_accepted_vision_event(
    payload: Mapping[str, object],
) -> tuple[AcceptedVisionEvent | None, str | None]:
    frame_id = normalize_string(payload.get("frame_id"))
    if not frame_id:
        return None, "missing_frame_id"

    session_id = normalize_string(payload.get("session_id"))
    if not session_id:
        return None, "missing_session_id"

    capture_ts_ms = coerce_optional_int(payload.get("capture_ts_ms"))
    if capture_ts_ms is None or capture_ts_ms < 0:
        return None, "invalid_capture_ts_ms"

    analyzed_at_ms = coerce_optional_int(payload.get("analyzed_at_ms"))
    confidence = _coerce_confidence(payload.get("confidence"))
    if confidence is None:
        return None, "invalid_confidence"

    event: AcceptedVisionEvent = {
        "event_type": normalize_string(payload.get("event_type")) or "accepted_visual_observation",
        "frame_id": frame_id,
        "session_id": session_id,
        "capture_ts_ms": capture_ts_ms,
        "analyzed_at_ms": analyzed_at_ms,
        "provider": normalize_string(payload.get("provider")),
        "model": normalize_string(payload.get("model")),
        "scene_summary": normalize_string(payload.get("scene_summary")),
        "user_activity_guess": normalize_string(payload.get("user_activity_guess")),
        "entities": normalize_string_list(payload.get("entities")),
        "actions": normalize_string_list(payload.get("actions")),
        "visible_text": normalize_string_list(payload.get("visible_text")),
        "documents_seen": normalize_string_list(payload.get("documents_seen")),
        "salient_change": bool(payload.get("salient_change")),
        "confidence": confidence,
    }
    return event, None


def _coerce_confidence(value: object) -> float | None:
    if value in (None, ""):
        return 0.0
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0.0:
        return 0.0
    if parsed > 1.0:
        if parsed <= 100.0:
            return parsed / 100.0
        return 1.0
    return parsed
