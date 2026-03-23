from __future__ import annotations

import json
from collections.abc import Mapping
from time import time_ns
from typing import Any, TypedDict

from backend.memory.normalize import normalize_string

ALLOWED_MEMORY_CANDIDATE_SCOPES = frozenset({"user", "cross_session"})
ALLOWED_MEMORY_CANDIDATE_SECTION_HINTS = frozenset(
    {"identity", "preferences", "stable_facts", "ongoing_threads", "follow_ups", "recent_facts"}
)
ALLOWED_MEMORY_CANDIDATE_STABILITY = frozenset({"stable", "semi_stable"})


class MemoryCandidate(TypedDict):
    session_id: str
    scope: str
    section_hint: str
    fact: str
    stability: str
    confidence: float
    captured_at_ms: int
    source: str


def build_memory_candidate(
    *,
    session_id: str,
    scope: object,
    section_hint: object,
    fact: object,
    stability: object,
    confidence: object,
    source: str = "realtime_capture_memory_candidate",
    captured_at_ms: int | None = None,
) -> MemoryCandidate | None:
    normalized_scope = normalize_string(scope).lower()
    normalized_section_hint = normalize_string(section_hint).lower()
    normalized_fact = normalize_string(fact)
    normalized_stability = normalize_string(stability).lower()
    normalized_source = normalize_string(source) or "memory_candidate"
    normalized_session_id = normalize_string(session_id)
    normalized_confidence = _coerce_confidence(confidence)
    if (
        not normalized_session_id
        or normalized_scope not in ALLOWED_MEMORY_CANDIDATE_SCOPES
        or normalized_section_hint not in ALLOWED_MEMORY_CANDIDATE_SECTION_HINTS
        or not normalized_fact
        or normalized_stability not in ALLOWED_MEMORY_CANDIDATE_STABILITY
        or normalized_confidence is None
    ):
        return None
    return MemoryCandidate(
        session_id=normalized_session_id,
        scope=normalized_scope,
        section_hint=normalized_section_hint,
        fact=normalized_fact,
        stability=normalized_stability,
        confidence=normalized_confidence,
        captured_at_ms=captured_at_ms if isinstance(captured_at_ms, int) else _now_ms(),
        source=normalized_source,
    )


def coerce_memory_candidate(payload: Mapping[str, object]) -> tuple[MemoryCandidate | None, str | None]:
    candidate = build_memory_candidate(
        session_id=payload.get("session_id"),
        scope=payload.get("scope"),
        section_hint=payload.get("section_hint"),
        fact=payload.get("fact"),
        stability=payload.get("stability"),
        confidence=payload.get("confidence"),
        source=normalize_string(payload.get("source")) or "memory_candidate_log",
        captured_at_ms=_coerce_optional_int(payload.get("captured_at_ms")),
    )
    if candidate is None:
        return None, "invalid_memory_candidate"
    return candidate, None


def render_memory_candidate(candidate: MemoryCandidate) -> dict[str, Any]:
    return dict(candidate)


def render_memory_candidate_ndjson(candidates: list[MemoryCandidate]) -> str:
    if not candidates:
        return ""
    return "\n".join(
        json.dumps(render_memory_candidate(candidate), ensure_ascii=True, sort_keys=True)
        for candidate in candidates
    ) + "\n"


def _coerce_confidence(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0.0:
        return 0.0
    if parsed > 1.0:
        return 1.0
    return parsed


def _coerce_optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _now_ms() -> int:
    return time_ns() // 1_000_000
