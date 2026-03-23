from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import ValidationError

from backend.core.settings import Settings
from backend.vision.contracts import (
    ProviderObservationPayload,
    VisionFrameContext,
    VisionObservation,
    VisionProviderError,
    parse_provider_observation_payload,
)
from backend.vision.providers.shared import (
    DEFAULT_VISION_MAX_TOKENS,
    DEFAULT_VISION_TEMPERATURE,
    DEFAULT_VISION_TOP_P,
    VISION_SYSTEM_PROMPT,
    build_base64_data,
    build_user_prompt,
    normalize_observation,
    post_json_with_vision_errors,
    safe_json_excerpt,
)

DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"
_GEMINI_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _normalize_gemini_model_name(model_name: str) -> str:
    normalized = model_name.strip()
    if normalized.startswith("models/"):
        normalized = normalized.split("/", 1)[1].strip()

    if not normalized:
        raise RuntimeError(
            "VISION_GEMINI_MODEL is required when VISION_MEMORY_PROVIDER=gemini"
        )
    if any(character.isspace() for character in normalized):
        raise RuntimeError(
            "VISION_GEMINI_MODEL must not contain whitespace when VISION_MEMORY_PROVIDER=gemini"
        )
    if any(character in normalized for character in ["/", "\\", "?", "#", "&", "="]):
        raise RuntimeError(
            "VISION_GEMINI_MODEL contains unsupported delimiter characters for VISION_MEMORY_PROVIDER=gemini"
        )
    if not _GEMINI_MODEL_PATTERN.fullmatch(normalized):
        raise RuntimeError(
            "VISION_GEMINI_MODEL contains unsupported characters for VISION_MEMORY_PROVIDER=gemini"
        )
    return normalized


def validate_gemini_vision_settings(settings: Settings) -> None:
    settings.validate_vision_provider_credentials(provider="gemini")
    _normalize_gemini_model_name(settings.resolve_vision_provider_model(provider="gemini") or "")


def build_gemini_vision_analyzer(*, settings: Settings) -> "GeminiVisionAnalyzer":
    return GeminiVisionAnalyzer(
        api_key=settings.require_vision_provider_api_key(provider="gemini"),
        model_name=_normalize_gemini_model_name(
            settings.resolve_vision_provider_model(provider="gemini") or ""
        ),
        base_url=settings.resolve_vision_provider_base_url(provider="gemini"),
    )


@dataclass(slots=True)
class GeminiVisionAnalyzer:
    api_key: str
    model_name: str
    base_url: str | None = None
    provider_name: str = field(default="gemini", init=False)
    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.model_name = _normalize_gemini_model_name(self.model_name)

    async def startup(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=(self.base_url or DEFAULT_GEMINI_BASE_URL).rstrip("/"),
                headers={
                    "x-goog-api-key": self.api_key,
                    "Accept": "application/json",
                },
                timeout=httpx.Timeout(connect=8.0, read=20.0, write=20.0, pool=8.0),
            )

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def analyze_frame(
        self,
        *,
        image_bytes: bytes,
        frame_context: VisionFrameContext,
        image_media_type: str = "image/jpeg",
    ) -> VisionObservation:
        client = await self._get_client()
        response = await self._post_completion(
            client=client,
            request_body=self._build_request_body(
                image_bytes=image_bytes,
                frame_context=frame_context,
                image_media_type=image_media_type,
            ),
        )

        try:
            response_json = response.json()
        except ValueError as exc:
            raise VisionProviderError(
                status_code=response.status_code,
                provider_error_code="provider_invalid_json_response",
                provider_message="Vision provider returned a non-JSON response body",
                payload_excerpt=response.text[:400] if response.text else None,
            ) from exc

        try:
            payload = self._extract_provider_payload(response_json)
        except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
            raise VisionProviderError(
                status_code=response.status_code,
                provider_error_code="provider_payload_invalid_json",
                provider_message="Vision provider returned an observation payload that could not be parsed",
                payload_excerpt=safe_json_excerpt(response_json),
            ) from exc
        return normalize_observation(payload=payload, frame_context=frame_context)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            await self.startup()
        assert self._client is not None
        return self._client

    async def _post_completion(
        self,
        *,
        client: httpx.AsyncClient,
        request_body: dict[str, Any],
    ) -> httpx.Response:
        model = quote(self.model_name.strip(), safe="")
        endpoint = f"/v1beta/models/{model}:generateContent"
        return await post_json_with_vision_errors(
            client=client,
            url=endpoint,
            request_body=request_body,
        )

    def _build_request_body(
        self,
        *,
        image_bytes: bytes,
        frame_context: VisionFrameContext,
        image_media_type: str,
    ) -> dict[str, Any]:
        return {
            "system_instruction": {
                "parts": [{"text": VISION_SYSTEM_PROMPT}],
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": build_user_prompt(frame_context=frame_context)},
                        {
                            "inline_data": {
                                "mime_type": image_media_type,
                                "data": build_base64_data(image_bytes=image_bytes),
                            }
                        },
                    ],
                }
            ],
            "generationConfig": {
                "temperature": DEFAULT_VISION_TEMPERATURE,
                "topP": DEFAULT_VISION_TOP_P,
                "maxOutputTokens": DEFAULT_VISION_MAX_TOKENS,
                "responseMimeType": "application/json",
            },
        }

    def _extract_provider_payload(self, response_json: dict[str, Any]) -> ProviderObservationPayload:
        candidates = response_json.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ValueError("Gemini response did not include candidates")

        candidate = candidates[0]
        if not isinstance(candidate, dict):
            raise ValueError("Gemini response candidate payload had an unsupported shape")

        content = candidate.get("content")
        if not isinstance(content, dict):
            raise ValueError("Gemini response did not include content")

        parts = content.get("parts")
        if not isinstance(parts, list) or not parts:
            raise ValueError("Gemini response content did not include parts")

        text_parts: list[str] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            text_value = part.get("text")
            if isinstance(text_value, str) and text_value.strip():
                text_parts.append(text_value)

        if not text_parts:
            raise ValueError("Gemini response content parts did not include text")

        return parse_provider_observation_payload("\n".join(text_parts))
