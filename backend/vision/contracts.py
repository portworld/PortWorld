from __future__ import annotations

import json
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _normalize_string(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        items = [value]
    normalized: list[str] = []
    for item in items:
        text = str(item).strip()
        if text:
            normalized.append(text)
    return normalized


def _normalize_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, list):
        return len(value) > 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "y", "1", "changed", "salient"}:
            return True
        if normalized in {"false", "no", "n", "0", "unchanged", "none", ""}:
            return False
    return bool(value)


def _normalize_confidence(value: object) -> float:
    if isinstance(value, (int, float)):
        confidence = float(value)
    elif isinstance(value, str):
        candidate = value.strip().rstrip("%")
        confidence = float(candidate)
        if value.strip().endswith("%"):
            confidence /= 100.0
    else:
        raise TypeError("confidence must be numeric")

    if confidence < 0.0:
        return 0.0
    if confidence > 1.0:
        if confidence <= 100.0:
            return confidence / 100.0
        return 1.0
    return confidence


def _extract_json_object_text(payload: str) -> str:
    stripped = payload.strip()
    if not stripped:
        raise json.JSONDecodeError("Empty provider payload", payload, 0)

    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].startswith("```"):
            stripped = "\n".join(lines[1:-1]).strip()
            if stripped.lower().startswith("json\n"):
                stripped = stripped[5:].strip()

    try:
        json.loads(stripped)
        return stripped
    except json.JSONDecodeError:
        pass

    object_start = stripped.find("{")
    object_end = stripped.rfind("}")
    if object_start == -1 or object_end == -1 or object_end <= object_start:
        raise json.JSONDecodeError("No JSON object found in provider payload", stripped, 0)

    candidate = stripped[object_start : object_end + 1]
    json.loads(candidate)
    return candidate


class VisionFrameContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frame_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    capture_ts_ms: int
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)


class VisionObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frame_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    capture_ts_ms: int
    scene_summary: str = Field(min_length=1)
    user_activity_guess: str = Field(default="")
    entities: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    visible_text: list[str] = Field(default_factory=list)
    documents_seen: list[str] = Field(default_factory=list)
    salient_change: bool
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator(
        "scene_summary",
        "user_activity_guess",
        mode="before",
    )
    @classmethod
    def _normalize_string_field(cls, value: object) -> str:
        return _normalize_string(value)

    @field_validator(
        "entities",
        "actions",
        "visible_text",
        "documents_seen",
        mode="before",
    )
    @classmethod
    def _normalize_string_list(cls, value: object) -> list[str]:
        return _normalize_string_list(value)


class ProviderObservationPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    scene_summary: str = Field(min_length=1)
    user_activity_guess: str = Field(default="")
    entities: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    visible_text: list[str] = Field(default_factory=list)
    documents_seen: list[str] = Field(default_factory=list)
    salient_change: bool
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator(
        "scene_summary",
        "user_activity_guess",
        mode="before",
    )
    @classmethod
    def _normalize_string_field(cls, value: object) -> str:
        return _normalize_string(value)

    @field_validator(
        "entities",
        "actions",
        "visible_text",
        "documents_seen",
        mode="before",
    )
    @classmethod
    def _normalize_string_list(cls, value: object) -> list[str]:
        return _normalize_string_list(value)

    @field_validator("salient_change", mode="before")
    @classmethod
    def _normalize_salient_change(cls, value: object) -> bool:
        return _normalize_bool(value)

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalize_confidence_field(cls, value: object) -> float:
        return _normalize_confidence(value)


def parse_provider_observation_payload(payload: str | bytes | dict[str, object]) -> ProviderObservationPayload:
    if isinstance(payload, dict):
        return ProviderObservationPayload.model_validate(payload)
    if isinstance(payload, bytes):
        return ProviderObservationPayload.model_validate_json(payload)
    return ProviderObservationPayload.model_validate(json.loads(_extract_json_object_text(payload)))


class VisionAnalyzer(Protocol):
    provider_name: str
    model_name: str

    async def startup(self) -> None: ...

    async def shutdown(self) -> None: ...

    async def analyze_frame(
        self,
        *,
        image_bytes: bytes,
        frame_context: VisionFrameContext,
        image_media_type: str = "image/jpeg",
    ) -> VisionObservation: ...


class VisionRateLimitError(RuntimeError):
    def __init__(
        self,
        *,
        retry_after_seconds: float | None = None,
        status_code: int = 429,
        provider_error_code: str | None = None,
        provider_message: str | None = None,
        payload_excerpt: str | None = None,
    ) -> None:
        super().__init__("Vision provider returned rate-limited response")
        self.retry_after_seconds = retry_after_seconds
        self.status_code = status_code
        self.provider_error_code = provider_error_code
        self.provider_message = provider_message
        self.payload_excerpt = payload_excerpt


class VisionProviderError(RuntimeError):
    def __init__(
        self,
        *,
        status_code: int | None = None,
        provider_error_code: str | None = None,
        provider_message: str | None = None,
        payload_excerpt: str | None = None,
    ) -> None:
        super().__init__(provider_message or "Vision provider request failed")
        self.status_code = status_code
        self.provider_error_code = provider_error_code
        self.provider_message = provider_message
        self.payload_excerpt = payload_excerpt
