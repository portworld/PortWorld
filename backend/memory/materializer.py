from __future__ import annotations

from typing import Any

from backend.core.storage import now_ms
from backend.memory.events import AcceptedVisionEvent
from backend.memory.normalize import coerce_optional_int, normalize_string, normalize_string_list
from backend.vision.contracts import VisionObservation


MAX_LIST_ITEMS = 10
MAX_TRANSITIONS = 8
MAX_SUMMARY_SCENES = 3
UNCERTAINTY_CONFIDENCE_THRESHOLD = 0.55


def build_accepted_vision_event(
    *,
    observation: VisionObservation,
    provider: str,
    model: str,
    analyzed_at_ms: int | None = None,
) -> AcceptedVisionEvent:
    return {
        "event_type": "accepted_visual_observation",
        "frame_id": observation.frame_id,
        "session_id": observation.session_id,
        "capture_ts_ms": observation.capture_ts_ms,
        "analyzed_at_ms": analyzed_at_ms or now_ms(),
        "provider": provider,
        "model": model,
        "scene_summary": observation.scene_summary,
        "user_activity_guess": observation.user_activity_guess,
        "entities": list(observation.entities),
        "actions": list(observation.actions),
        "visible_text": list(observation.visible_text),
        "documents_seen": list(observation.documents_seen),
        "salient_change": observation.salient_change,
        "confidence": observation.confidence,
    }


def build_short_term_memory(
    *,
    session_id: str,
    accepted_events: list[AcceptedVisionEvent],
    window_seconds: int,
) -> tuple[dict[str, Any], str]:
    if accepted_events:
        latest_capture_ts_ms = max(event["capture_ts_ms"] for event in accepted_events)
        window_start_ts_ms = max(0, latest_capture_ts_ms - window_seconds * 1000)
        window_events = [event for event in accepted_events if event["capture_ts_ms"] >= window_start_ts_ms]
        latest_event = _latest_event_by_capture_ts(window_events)
    else:
        latest_capture_ts_ms = 0
        window_start_ts_ms = 0
        window_events = []
        latest_event = None

    payload = {
        "session_id": session_id,
        "window_start_ts_ms": window_start_ts_ms,
        "window_end_ts_ms": latest_capture_ts_ms,
        "current_scene_summary": latest_event["scene_summary"] if latest_event else "",
        "recent_entities": _unique_recent_values(window_events, "entities"),
        "recent_actions": _unique_recent_values(window_events, "actions"),
        "recent_visible_text": _unique_recent_values(window_events, "visible_text"),
        "recent_documents": _unique_recent_values(window_events, "documents_seen"),
        "source_frame_ids": [event["frame_id"] for event in window_events],
    }

    markdown_lines = [
        "# Short-Term Visual Memory",
        "",
        f"Current scene: {payload['current_scene_summary'] or 'No accepted observations yet.'}",
        f"Source frames: {', '.join(payload['source_frame_ids']) if payload['source_frame_ids'] else 'none'}",
        f"Recent entities: {', '.join(payload['recent_entities']) if payload['recent_entities'] else 'none'}",
        f"Recent actions: {', '.join(payload['recent_actions']) if payload['recent_actions'] else 'none'}",
        f"Visible text: {', '.join(payload['recent_visible_text']) if payload['recent_visible_text'] else 'none'}",
        f"Documents seen: {', '.join(payload['recent_documents']) if payload['recent_documents'] else 'none'}",
        "",
    ]
    return payload, "\n".join(markdown_lines)


def build_session_memory_rollup(
    *,
    session_id: str,
    previous_memory: dict[str, Any],
    recent_events: list[AcceptedVisionEvent],
) -> tuple[dict[str, Any], str]:
    previous_transitions = normalize_string_list(previous_memory.get("notable_transitions"))
    previous_entities = normalize_string_list(previous_memory.get("recurring_entities"))
    previous_documents = normalize_string_list(previous_memory.get("documents_seen"))
    recent_activities = [
        normalize_string(event.get("user_activity_guess")) for event in recent_events
    ]
    recent_scene_summaries = [
        normalize_string(event.get("scene_summary")) for event in recent_events
    ]
    recent_scene_summaries = [summary for summary in recent_scene_summaries if summary]
    latest_activity = _last_non_empty(recent_activities) or normalize_string(
        previous_memory.get("current_task_guess")
    )
    environment_summary = _build_environment_summary(
        previous_summary=normalize_string(previous_memory.get("environment_summary")),
        recent_scene_summaries=recent_scene_summaries,
    )
    recurring_entities = _merge_unique(previous_entities, _unique_recent_values(recent_events, "entities"))
    documents_seen = _merge_unique(previous_documents, _unique_recent_values(recent_events, "documents_seen"))
    notable_transitions = _build_notable_transitions(
        previous_transitions=previous_transitions,
        recent_events=recent_events,
    )
    open_uncertainties = _build_open_uncertainties(recent_events)

    started_at_ms = coerce_optional_int(previous_memory.get("started_at_ms"))
    if started_at_ms is None:
        started_at_ms = min(event["capture_ts_ms"] for event in recent_events) if recent_events else 0

    payload = {
        "session_id": session_id,
        "started_at_ms": started_at_ms,
        "updated_at_ms": now_ms(),
        "current_task_guess": latest_activity,
        "environment_summary": environment_summary,
        "recurring_entities": recurring_entities,
        "documents_seen": documents_seen,
        "notable_transitions": notable_transitions,
        "open_uncertainties": open_uncertainties,
        "summary_text": _build_session_summary_text(
            current_task_guess=latest_activity,
            environment_summary=environment_summary,
            recurring_entities=recurring_entities,
            documents_seen=documents_seen,
            notable_transitions=notable_transitions,
        ),
    }

    markdown_lines = [
        "# Session Memory",
        "",
        f"Current task guess: {payload['current_task_guess'] or 'Unknown'}",
        f"Environment summary: {payload['environment_summary'] or 'Unknown'}",
        f"Recurring entities: {', '.join(payload['recurring_entities']) if payload['recurring_entities'] else 'none'}",
        f"Documents seen: {', '.join(payload['documents_seen']) if payload['documents_seen'] else 'none'}",
        f"Notable transitions: {'; '.join(payload['notable_transitions']) if payload['notable_transitions'] else 'none'}",
        f"Open uncertainties: {'; '.join(payload['open_uncertainties']) if payload['open_uncertainties'] else 'none'}",
        "",
        payload["summary_text"] or "",
        "",
    ]
    return payload, "\n".join(markdown_lines)


def _unique_recent_values(events: list[AcceptedVisionEvent], key: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for event in reversed(events):
        for value in normalize_string_list(event.get(key)):
            lowered = value.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            result.append(value)
            if len(result) >= MAX_LIST_ITEMS:
                return result
    return result


def _merge_unique(existing: list[str], new_values: list[str]) -> list[str]:
    seen = {value.lower() for value in existing}
    merged = list(existing)
    for value in new_values:
        lowered = value.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        merged.append(value)
        if len(merged) >= MAX_LIST_ITEMS:
            break
    return merged


def _build_environment_summary(*, previous_summary: str, recent_scene_summaries: list[str]) -> str:
    if recent_scene_summaries:
        unique_summaries: list[str] = []
        seen: set[str] = set()
        for summary in reversed(recent_scene_summaries):
            lowered = summary.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            unique_summaries.append(summary)
            if len(unique_summaries) >= MAX_SUMMARY_SCENES:
                break
        return " | ".join(unique_summaries)
    return previous_summary


def _build_notable_transitions(
    *,
    previous_transitions: list[str],
    recent_events: list[AcceptedVisionEvent],
) -> list[str]:
    transitions = list(previous_transitions)
    seen = {item.lower() for item in transitions}
    for event in recent_events:
        if not event.get("salient_change"):
            continue
        summary = normalize_string(event.get("scene_summary"))
        activity = normalize_string(event.get("user_activity_guess"))
        parts = [part for part in [summary, activity] if part]
        if not parts:
            continue
        transition = " -> ".join(parts) if len(parts) > 1 else parts[0]
        lowered = transition.lower()
        if transition and lowered not in seen:
            transitions.append(transition)
            seen.add(lowered)
    return transitions[-MAX_TRANSITIONS:]


def _build_open_uncertainties(recent_events: list[AcceptedVisionEvent]) -> list[str]:
    uncertainties: list[str] = []
    for event in recent_events:
        confidence = float(event.get("confidence") or 0.0)
        if confidence >= UNCERTAINTY_CONFIDENCE_THRESHOLD:
            continue
        frame_id = normalize_string(event.get("frame_id"))
        summary = normalize_string(event.get("scene_summary"))
        statement = f"Low-confidence observation at {frame_id}: {summary}" if frame_id else f"Low-confidence observation: {summary}"
        uncertainties.append(statement)
    unique: list[str] = []
    seen: set[str] = set()
    for item in uncertainties:
        lowered = item.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        unique.append(item)
    return unique[:MAX_LIST_ITEMS]


def _build_session_summary_text(
    *,
    current_task_guess: str,
    environment_summary: str,
    recurring_entities: list[str],
    documents_seen: list[str],
    notable_transitions: list[str],
) -> str:
    parts: list[str] = []
    if current_task_guess:
        parts.append(f"User appears to be {current_task_guess}.")
    if environment_summary:
        parts.append(f"Environment: {environment_summary}.")
    if recurring_entities:
        parts.append(f"Recurring entities: {', '.join(recurring_entities)}.")
    if documents_seen:
        parts.append(f"Documents seen: {', '.join(documents_seen)}.")
    if notable_transitions:
        parts.append(f"Recent transitions: {'; '.join(notable_transitions)}.")
    return " ".join(parts)


def _last_non_empty(values: list[str]) -> str:
    for value in reversed(values):
        if value:
            return value
    return ""


def _latest_event_by_capture_ts(events: list[AcceptedVisionEvent]) -> AcceptedVisionEvent | None:
    latest_event: AcceptedVisionEvent | None = None
    latest_capture_ts_ms = -1
    for event in events:
        capture_ts_ms = event["capture_ts_ms"]
        if capture_ts_ms >= latest_capture_ts_ms:
            latest_capture_ts_ms = capture_ts_ms
            latest_event = event
    return latest_event
