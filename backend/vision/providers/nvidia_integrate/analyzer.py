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
    extract_json_object_text,
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
)

DEFAULT_NVIDIA_INTEGRATE_BASE_URL = "https://integrate.api.nvidia.com"
NVIDIA_FALLBACK_MAX_TOKENS = 384
NVIDIA_FALLBACK_SYSTEM_PROMPT = (
    f"{VISION_SYSTEM_PROMPT} "
    "Return only a raw JSON object with no markdown and no code fences. "
    "user_activity_guess must be a single short string, not an array. "
    "Use arrays only for entities, actions, visible_text, and documents_seen."
)


def validate_nvidia_integrate_vision_settings(settings: Settings) -> None:
    settings.validate_vision_provider_credentials(provider="nvidia_integrate")
    model_name = (settings.resolve_vision_provider_model(provider="nvidia_integrate") or "").strip()
    if not model_name:
        raise RuntimeError(
            "VISION_NVIDIA_MODEL is required when VISION_MEMORY_PROVIDER=nvidia_integrate"
        )


def build_nvidia_integrate_vision_analyzer(*, settings: Settings) -> "NvidiaIntegrateVisionAnalyzer":
    return NvidiaIntegrateVisionAnalyzer(
        api_key=settings.require_vision_provider_api_key(provider="nvidia_integrate"),
        model_name=settings.resolve_vision_provider_model(provider="nvidia_integrate") or "",
        base_url=settings.resolve_vision_provider_base_url(provider="nvidia_integrate"),
        request_timeout_seconds=settings.vision_provider_timeout_seconds,
    )


@dataclass(slots=True)
class NvidiaIntegrateVisionAnalyzer(OpenAICompatibleVisionAnalyzerBase):
    api_key: str
    model_name: str
    base_url: str | None = None
    provider_name: str = field(default="nvidia_integrate", init=False)

    def __post_init__(self) -> None:
        if "mistral" in self.model_name.strip().lower():
            self._supports_response_format = False

    @property
    def default_base_url(self) -> str:
        return DEFAULT_NVIDIA_INTEGRATE_BASE_URL

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
                {
                    "role": "system",
                    "content": (
                        VISION_SYSTEM_PROMPT
                        if include_response_format
                        else NVIDIA_FALLBACK_SYSTEM_PROMPT
                    ),
                },
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
        payload[token_field_name] = (
            NVIDIA_FALLBACK_MAX_TOKENS
            if not include_response_format
            else DEFAULT_VISION_MAX_TOKENS
        )
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
            raise ValueError("NVIDIA Integrate response did not include choices")

        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise ValueError("NVIDIA Integrate response did not include a message payload")

        payload_text = coalesce_text_content(message.get("content"))
        try:
            normalized_payload = _normalize_nvidia_fallback_payload(payload_text)
            return parse_provider_observation_payload(normalized_payload)
        except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
            raise build_provider_payload_parse_error(
                status_code=status_code,
                payload_text=payload_text,
                payload_excerpt=payload_text,
            ) from exc


def _normalize_nvidia_fallback_payload(payload_text: str) -> dict[str, object]:
    payload = json.loads(extract_json_object_text(payload_text))
    if not isinstance(payload, dict):
        raise ValueError("NVIDIA fallback payload was not a JSON object")

    normalized = dict(payload)
    user_activity_guess = normalized.get("user_activity_guess")
    if isinstance(user_activity_guess, list):
        normalized["user_activity_guess"] = _collapse_string_list(user_activity_guess)

    for field_name in ("entities", "actions", "visible_text", "documents_seen"):
        value = normalized.get(field_name)
        if value is None:
            normalized[field_name] = []
            continue
        if isinstance(value, list):
            normalized[field_name] = [str(item).strip() for item in value if str(item).strip()]
            continue
        text = str(value).strip()
        normalized[field_name] = [text] if text else []

    return normalized


def _collapse_string_list(values: list[object]) -> str:
    text_values = [str(value).strip() for value in values if str(value).strip()]
    return ", ".join(text_values)
