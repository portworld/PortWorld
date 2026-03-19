from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

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
    VISION_SYSTEM_PROMPT,
    build_base64_data,
    build_user_prompt,
    normalize_observation,
    post_json_with_vision_errors,
    safe_json_excerpt,
)

DEFAULT_CLAUDE_BASE_URL = "https://api.anthropic.com"
DEFAULT_ANTHROPIC_VERSION = "2023-06-01"


def validate_claude_vision_settings(settings: Settings) -> None:
    settings.validate_vision_provider_credentials(provider="claude")
    model_name = settings.vision_memory_model.strip()
    if not model_name:
        raise RuntimeError(
            "VISION_MEMORY_MODEL is required when VISION_MEMORY_PROVIDER=claude"
        )


def build_claude_vision_analyzer(*, settings: Settings) -> "ClaudeVisionAnalyzer":
    return ClaudeVisionAnalyzer(
        api_key=settings.require_vision_provider_api_key(provider="claude"),
        model_name=settings.vision_memory_model,
        base_url=settings.resolve_vision_provider_base_url(provider="claude"),
    )


@dataclass(slots=True)
class ClaudeVisionAnalyzer:
    api_key: str
    model_name: str
    base_url: str | None = None
    provider_name: str = field(default="claude", init=False)
    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)

    async def startup(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=(self.base_url or DEFAULT_CLAUDE_BASE_URL).rstrip("/"),
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": DEFAULT_ANTHROPIC_VERSION,
                    "content-type": "application/json",
                    "accept": "application/json",
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
        return await post_json_with_vision_errors(
            client=client,
            url="/v1/messages",
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
            "model": self.model_name,
            "system": VISION_SYSTEM_PROMPT,
            "max_tokens": DEFAULT_VISION_MAX_TOKENS,
            "temperature": DEFAULT_VISION_TEMPERATURE,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": build_user_prompt(frame_context=frame_context)},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": image_media_type,
                                "data": build_base64_data(image_bytes=image_bytes),
                            },
                        },
                    ],
                }
            ],
        }

    def _extract_provider_payload(self, response_json: dict[str, Any]) -> ProviderObservationPayload:
        content = response_json.get("content")
        if not isinstance(content, list) or not content:
            raise ValueError("Claude response did not include content")

        text_parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "text":
                continue
            text_value = item.get("text")
            if isinstance(text_value, str) and text_value.strip():
                text_parts.append(text_value)

        if not text_parts:
            raise ValueError("Claude response content did not include text")

        return parse_provider_observation_payload("\n".join(text_parts))
