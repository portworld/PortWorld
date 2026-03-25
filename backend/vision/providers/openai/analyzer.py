from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping

import httpx
from pydantic import ValidationError

from backend.core.settings import Settings
from backend.vision.contracts import (
    ProviderObservationPayload,
    VisionFrameContext,
    parse_provider_observation_payload,
)
from backend.vision.providers.openai_compatible import (
    OpenAICompatibleVisionAnalyzerBase,
    post_openai_compatible_completion,
)
from backend.vision.providers.shared import (
    DEFAULT_VISION_MAX_TOKENS,
    DEFAULT_VISION_TEMPERATURE,
    DEFAULT_VISION_TOP_P,
    build_provider_payload_parse_error,
    VISION_SYSTEM_PROMPT,
    build_data_url,
    build_user_prompt,
    coalesce_text_content,
    extract_provider_content_excerpt_from_chat_choices,
)

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com"


def validate_openai_vision_settings(settings: Settings) -> None:
    settings.validate_vision_provider_credentials(provider="openai")
    model_name = (settings.resolve_vision_provider_model(provider="openai") or "").strip()
    if not model_name:
        raise RuntimeError(
            "VISION_OPENAI_MODEL is required when VISION_MEMORY_PROVIDER=openai"
        )


def build_openai_vision_analyzer(*, settings: Settings) -> "OpenAIVisionAnalyzer":
    return OpenAIVisionAnalyzer(
        api_key=settings.require_vision_provider_api_key(provider="openai"),
        model_name=settings.resolve_vision_provider_model(provider="openai") or "",
        base_url=settings.resolve_vision_provider_base_url(provider="openai"),
    )


@dataclass(slots=True)
class OpenAIVisionAnalyzer(OpenAICompatibleVisionAnalyzerBase):
    api_key: str
    model_name: str
    base_url: str | None = None
    provider_name: str = field(default="openai", init=False)

    @property
    def default_base_url(self) -> str:
        return DEFAULT_OPENAI_BASE_URL

    def build_client_headers(self) -> Mapping[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

    async def post_completion(
        self,
        *,
        client: httpx.AsyncClient,
        request_body: dict[str, Any],
    ) -> httpx.Response:
        return await post_openai_compatible_completion(
            client=client,
            url="/v1/chat/completions",
            request_body=request_body,
        )

    def build_request_body(
        self,
        *,
        image_bytes: bytes,
        frame_context: VisionFrameContext,
        image_media_type: str,
        include_response_format: bool,
        use_legacy_max_tokens: bool,
    ) -> dict[str, Any]:
        data_url = build_data_url(image_bytes=image_bytes, image_media_type=image_media_type)
        payload: dict[str, Any] = {
            "model": self.model_name,
            "temperature": DEFAULT_VISION_TEMPERATURE,
            "top_p": DEFAULT_VISION_TOP_P,
            "messages": [
                {"role": "system", "content": VISION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": build_user_prompt(frame_context=frame_context)},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
        }
        token_field_name = "max_tokens" if use_legacy_max_tokens else "max_completion_tokens"
        payload[token_field_name] = DEFAULT_VISION_MAX_TOKENS
        if include_response_format:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def extract_provider_payload(
        self,
        response_json: dict[str, Any],
        *,
        status_code: int | None = None,
    ) -> ProviderObservationPayload:
        choices = response_json.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("OpenAI response did not include choices")

        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise ValueError("OpenAI response did not include a message payload")

        payload_text = coalesce_text_content(message.get("content"))
        try:
            return parse_provider_observation_payload(payload_text)
        except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
            raise build_provider_payload_parse_error(
                status_code=status_code,
                payload_text=payload_text,
                payload_excerpt=extract_provider_content_excerpt_from_chat_choices(response_json),
            ) from exc
