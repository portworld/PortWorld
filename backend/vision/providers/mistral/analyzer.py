from __future__ import annotations

import base64
import datetime as dt
import email.utils
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
    VisionRateLimitError,
    parse_provider_observation_payload,
)

DEFAULT_MISTRAL_BASE_URL = "https://api.mistral.ai"
DEFAULT_VISION_TEMPERATURE = 0.0
DEFAULT_VISION_TOP_P = 0.1
DEFAULT_VISION_MAX_TOKENS = 280

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a vision observation service for a realtime wearable assistant. "
    "Return exactly one compact JSON object with keys: "
    "scene_summary, user_activity_guess, entities, actions, visible_text, documents_seen, salient_change, confidence. "
    "Do not include markdown, code fences, or extra commentary. "
    "Keep scene_summary short and factual. "
    "Use arrays of short strings for entities, actions, visible_text, and documents_seen. "
    "Set salient_change to the JSON boolean true or false. "
    "Set confidence as a JSON number between 0.0 and 1.0."
)


def validate_mistral_vision_settings(settings: Settings) -> None:
    settings.validate_vision_provider_credentials(provider="mistral")


def build_mistral_vision_analyzer(*, settings: Settings) -> "MistralVisionAnalyzer":
    return MistralVisionAnalyzer(
        api_key=settings.require_vision_provider_api_key(provider="mistral"),
        model_name=settings.vision_memory_model,
        base_url=settings.resolve_vision_provider_base_url(provider="mistral"),
    )


@dataclass(slots=True)
class MistralVisionAnalyzer:
    api_key: str
    model_name: str
    base_url: str | None = None
    provider_name: str = field(default="mistral", init=False)
    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)
    _supports_response_format: bool = field(default=True, init=False, repr=False)

    async def startup(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=(self.base_url or DEFAULT_MISTRAL_BASE_URL).rstrip("/"),
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
        )
        try:
            response = await self._post_completion(client=client, request_body=request_body)
        except VisionProviderError as exc:
            if self._supports_response_format and _is_response_format_compatibility_error(exc):
                self._supports_response_format = False
                logger.warning(
                    "Vision provider rejected response_format; retrying without structured output provider=%s model=%s base_url=%s provider_message=%s",
                    self.provider_name,
                    self.model_name,
                    (self.base_url or DEFAULT_MISTRAL_BASE_URL).rstrip("/"),
                    (exc.provider_message or "").strip()[:220] or None,
                )
                fallback_body = self._build_request_body(
                    image_bytes=image_bytes,
                    frame_context=frame_context,
                    image_media_type=image_media_type,
                    include_response_format=False,
                )
                response = await self._post_completion(client=client, request_body=fallback_body)
            else:
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
                provider_message=(
                    "Vision provider returned an observation payload that could not be parsed"
                ),
                payload_excerpt=_extract_provider_content_excerpt(response_json),
            ) from exc
        return self._normalize_observation(payload=payload, frame_context=frame_context)

    async def _post_completion(
        self,
        *,
        client: httpx.AsyncClient,
        request_body: dict[str, Any],
    ) -> httpx.Response:
        try:
            response = await client.post(
                "/v1/chat/completions",
                json=request_body,
            )
        except httpx.ReadTimeout as exc:
            raise VisionProviderError(
                provider_error_code="provider_read_timeout",
                provider_message="Vision provider request timed out while waiting for a response",
            ) from exc
        except httpx.RequestError as exc:
            raise VisionProviderError(
                provider_error_code="provider_transport_error",
                provider_message=f"{type(exc).__name__}: {exc}",
            ) from exc
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            error_details = _extract_error_details(exc.response)
            if exc.response.status_code == 429:
                raise VisionRateLimitError(
                    retry_after_seconds=_parse_retry_after_seconds(exc.response),
                    status_code=429,
                    provider_error_code=error_details["provider_error_code"],
                    provider_message=error_details["provider_message"],
                    payload_excerpt=error_details["payload_excerpt"],
                ) from exc
            raise VisionProviderError(
                status_code=exc.response.status_code,
                provider_error_code=error_details["provider_error_code"],
                provider_message=error_details["provider_message"],
                payload_excerpt=error_details["payload_excerpt"],
            ) from exc
        return response

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            await self.startup()
        assert self._client is not None
        return self._client

    def _build_request_body(
        self,
        *,
        image_bytes: bytes,
        frame_context: VisionFrameContext,
        image_media_type: str,
        include_response_format: bool = True,
    ) -> dict[str, Any]:
        data_url = self._build_data_url(image_bytes=image_bytes, image_media_type=image_media_type)
        payload: dict[str, Any] = {
            "model": self.model_name,
            "temperature": DEFAULT_VISION_TEMPERATURE,
            "top_p": DEFAULT_VISION_TOP_P,
            "max_tokens": DEFAULT_VISION_MAX_TOKENS,
            "messages": [
                {
                    "role": "system",
                    "content": _SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": self._build_user_prompt(frame_context=frame_context),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": data_url,
                            },
                        },
                    ],
                },
            ],
        }
        if include_response_format:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _build_user_prompt(self, *, frame_context: VisionFrameContext) -> str:
        dimensions = []
        if frame_context.width is not None:
            dimensions.append(f"width={frame_context.width}")
        if frame_context.height is not None:
            dimensions.append(f"height={frame_context.height}")
        dimension_text = ", ".join(dimensions) if dimensions else "unknown dimensions"
        return (
            "Analyze this single still image for short-term wearable context. "
            f"Frame context: session_id={frame_context.session_id}, frame_id={frame_context.frame_id}, "
            f"capture_ts_ms={frame_context.capture_ts_ms}, {dimension_text}. "
            "Focus on what the user appears to be doing, prominent entities, readable text, "
            "and whether this frame likely represents a salient change from nearby context."
        )

    def _build_data_url(self, *, image_bytes: bytes, image_media_type: str) -> str:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        return f"data:{image_media_type};base64,{encoded}"

    def _extract_provider_payload(self, response_json: dict[str, Any]) -> ProviderObservationPayload:
        choices = response_json.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("Mistral response did not include choices")

        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise ValueError("Mistral response did not include a message payload")

        content = message.get("content")
        if isinstance(content, str):
            return parse_provider_observation_payload(content)
        if isinstance(content, list):
            return parse_provider_observation_payload(self._coalesce_content_list(content))

        raise ValueError("Mistral response message content had an unsupported shape")

    def _coalesce_content_list(self, content: list[Any]) -> str:
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                text_parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                text_value = item.get("text")
                if isinstance(text_value, str):
                    text_parts.append(text_value)
        if not text_parts:
            raise ValueError("Mistral response content list did not contain text")
        return "\n".join(text_parts)

    def _normalize_observation(
        self,
        *,
        payload: ProviderObservationPayload,
        frame_context: VisionFrameContext,
    ) -> VisionObservation:
        return VisionObservation.model_validate(
            {
                "frame_id": frame_context.frame_id,
                "session_id": frame_context.session_id,
                "capture_ts_ms": frame_context.capture_ts_ms,
                **payload.model_dump(),
            }
        )


def _parse_retry_after_seconds(response: httpx.Response) -> float | None:
    retry_after_raw = response.headers.get("Retry-After")
    if not retry_after_raw:
        return None
    candidate = retry_after_raw.strip()
    if not candidate:
        return None
    try:
        value = float(candidate)
        return value if value > 0 else None
    except ValueError:
        pass
    try:
        parsed_dt = email.utils.parsedate_to_datetime(candidate)
    except (TypeError, ValueError):
        return None
    if parsed_dt is None:
        return None
    if parsed_dt.tzinfo is None:
        parsed_dt = parsed_dt.replace(tzinfo=dt.timezone.utc)
    now = dt.datetime.now(tz=dt.timezone.utc)
    delta_seconds = (parsed_dt - now).total_seconds()
    return delta_seconds if delta_seconds > 0 else None


def _extract_error_details(response: httpx.Response) -> dict[str, str | None]:
    payload_excerpt: str | None = None
    provider_error_code: str | None = None
    provider_message: str | None = None

    raw_text = response.text.strip()
    if raw_text:
        payload_excerpt = raw_text[:400]

    try:
        payload = response.json()
    except ValueError:
        return {
            "provider_error_code": provider_error_code,
            "provider_message": provider_message,
            "payload_excerpt": payload_excerpt,
        }

    if isinstance(payload, dict):
        error_payload = payload.get("error")
        if isinstance(error_payload, dict):
            error_code = error_payload.get("code")
            if isinstance(error_code, str) and error_code.strip():
                provider_error_code = error_code.strip()
            message = error_payload.get("message")
            if isinstance(message, str) and message.strip():
                provider_message = message.strip()
        elif isinstance(error_payload, str) and error_payload.strip():
            provider_message = error_payload.strip()
        if payload_excerpt is None:
            payload_excerpt = str(payload)[:400]

    return {
        "provider_error_code": provider_error_code,
        "provider_message": provider_message,
        "payload_excerpt": payload_excerpt,
    }


def _extract_provider_content_excerpt(response_json: dict[str, Any]) -> str | None:
    choices = response_json.get("choices")
    if not isinstance(choices, list) or not choices:
        return str(response_json)[:400]

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return str(first_choice)[:400]

    message = first_choice.get("message")
    if not isinstance(message, dict):
        return str(first_choice)[:400]

    content = message.get("content")
    if content is None:
        return None
    return str(content)[:400]


def _is_response_format_compatibility_error(error: VisionProviderError) -> bool:
    if error.status_code != 400:
        return False
    code = (error.provider_error_code or "").strip().lower()
    message = (error.provider_message or "").strip().lower()
    if "response_format" in message:
        if not code:
            return True
        return "unknown_parameter" in code or "invalid_parameter" in code

    structured_output_markers = (
        "structured output backend",
        "structured outputs",
        "guidance",
        "xgrammar",
        "outlines",
        "tokenizer_mode='hf'",
        'tokenizer_mode="hf"',
        "mistral tokenizer is not supported",
    )
    if any(marker in message for marker in structured_output_markers):
        return True
    return False
