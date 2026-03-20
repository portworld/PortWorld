from __future__ import annotations

import json
import logging
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
    DEFAULT_VISION_TOP_P,
    VISION_SYSTEM_PROMPT,
    build_data_url,
    build_user_prompt,
    coalesce_text_content,
    extract_provider_content_excerpt_from_chat_choices,
    is_max_completion_tokens_compatibility_error,
    is_response_format_compatibility_error,
    normalize_observation,
    post_json_with_vision_errors,
    sanitize_sensitive_text,
    sanitize_url_for_logging,
)

DEFAULT_NVIDIA_INTEGRATE_BASE_URL = "https://integrate.api.nvidia.com"
NVIDIA_FALLBACK_SYSTEM_PROMPT = (
    f"{VISION_SYSTEM_PROMPT} "
    "Return only a raw JSON object with no markdown and no code fences. "
    "user_activity_guess must be a single short string, not an array. "
    "Use arrays only for entities, actions, visible_text, and documents_seen."
)

logger = logging.getLogger(__name__)


def validate_nvidia_integrate_vision_settings(settings: Settings) -> None:
    settings.validate_vision_provider_credentials(provider="nvidia_integrate")
    model_name = (settings.resolve_vision_provider_model(provider="nvidia_integrate") or "").strip()
    if not model_name:
        raise RuntimeError(
            "VISION_NVIDIA_MODEL is required when VISION_MEMORY_PROVIDER=nvidia_integrate "
            "(legacy fallback: VISION_MEMORY_MODEL)"
        )


def build_nvidia_integrate_vision_analyzer(*, settings: Settings) -> "NvidiaIntegrateVisionAnalyzer":
    return NvidiaIntegrateVisionAnalyzer(
        api_key=settings.require_vision_provider_api_key(provider="nvidia_integrate"),
        model_name=settings.resolve_vision_provider_model(provider="nvidia_integrate") or "",
        base_url=settings.resolve_vision_provider_base_url(provider="nvidia_integrate"),
    )


@dataclass(slots=True)
class NvidiaIntegrateVisionAnalyzer:
    api_key: str
    model_name: str
    base_url: str | None = None
    provider_name: str = field(default="nvidia_integrate", init=False)
    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)
    _supports_response_format: bool = field(default=True, init=False, repr=False)
    _uses_legacy_max_tokens: bool = field(default=False, init=False, repr=False)

    async def startup(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=(self.base_url or DEFAULT_NVIDIA_INTEGRATE_BASE_URL).rstrip("/"),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
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
        request_body = self._build_request_body(
            image_bytes=image_bytes,
            frame_context=frame_context,
            image_media_type=image_media_type,
            include_response_format=self._supports_response_format,
            use_legacy_max_tokens=self._uses_legacy_max_tokens,
        )

        while True:
            try:
                response = await self._post_completion(client=client, request_body=request_body)
                break
            except VisionProviderError as exc:
                base_url_for_log = sanitize_url_for_logging(
                    (self.base_url or DEFAULT_NVIDIA_INTEGRATE_BASE_URL).rstrip("/")
                )
                provider_message_excerpt = (sanitize_sensitive_text(exc.provider_message) or "")[
                    :220
                ] or None
                if (
                    not self._uses_legacy_max_tokens
                    and is_max_completion_tokens_compatibility_error(exc)
                ):
                    self._uses_legacy_max_tokens = True
                    logger.warning(
                        "Vision provider rejected max_completion_tokens; retrying with legacy max_tokens provider=%s model=%s base_url=%s provider_message=%s",
                        self.provider_name,
                        self.model_name,
                        base_url_for_log,
                        provider_message_excerpt,
                    )
                    request_body = self._build_request_body(
                        image_bytes=image_bytes,
                        frame_context=frame_context,
                        image_media_type=image_media_type,
                        include_response_format=self._supports_response_format,
                        use_legacy_max_tokens=True,
                    )
                    continue
                if self._supports_response_format and is_response_format_compatibility_error(exc):
                    self._supports_response_format = False
                    logger.warning(
                        "Vision provider rejected response_format; retrying without structured output provider=%s model=%s base_url=%s provider_message=%s",
                        self.provider_name,
                        self.model_name,
                        base_url_for_log,
                        provider_message_excerpt,
                    )
                    request_body = self._build_request_body(
                        image_bytes=image_bytes,
                        frame_context=frame_context,
                        image_media_type=image_media_type,
                        include_response_format=False,
                        use_legacy_max_tokens=self._uses_legacy_max_tokens,
                    )
                    continue
                raise

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
                payload_excerpt=extract_provider_content_excerpt_from_chat_choices(response_json),
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
            url="/v1/chat/completions",
            request_body=request_body,
        )

    def _build_request_body(
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
        payload[token_field_name] = DEFAULT_VISION_MAX_TOKENS
        if include_response_format:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _extract_provider_payload(self, response_json: dict[str, Any]) -> ProviderObservationPayload:
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
        except (json.JSONDecodeError, TypeError, ValueError, ValidationError):
            return parse_provider_observation_payload(payload_text)


def _normalize_nvidia_fallback_payload(payload_text: str) -> dict[str, object]:
    payload = json.loads(_extract_json_candidate(payload_text))
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


def _extract_json_candidate(payload_text: str) -> str:
    stripped = payload_text.strip()
    if not stripped:
        raise json.JSONDecodeError("Empty provider payload", payload_text, 0)

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


def _collapse_string_list(values: list[object]) -> str:
    text_values = [str(value).strip() for value in values if str(value).strip()]
    return ", ".join(text_values)
