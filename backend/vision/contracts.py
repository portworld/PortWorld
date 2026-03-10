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
    model_config = ConfigDict(extra="forbid")

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


def parse_provider_observation_payload(payload: str | bytes | dict[str, object]) -> ProviderObservationPayload:
    if isinstance(payload, dict):
        return ProviderObservationPayload.model_validate(payload)
    if isinstance(payload, bytes):
        return ProviderObservationPayload.model_validate_json(payload)
    return ProviderObservationPayload.model_validate(json.loads(payload))


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
    def __init__(self, *, retry_after_seconds: float | None = None) -> None:
        super().__init__("Vision provider returned rate-limited response")
        self.retry_after_seconds = retry_after_seconds
