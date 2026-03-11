from __future__ import annotations

import base64
import datetime as dt
import email.utils
from dataclasses import dataclass, field
from typing import Any

import httpx

from backend.vision.contracts import (
    ProviderObservationPayload,
    VisionFrameContext,
    VisionObservation,
    VisionProviderError,
    VisionRateLimitError,
    parse_provider_observation_payload,
)

DEFAULT_MISTRAL_BASE_URL = "https://api.mistral.ai"

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


@dataclass(slots=True)
class MistralVisionAnalyzer:
    api_key: str
    model_name: str
    base_url: str | None = None
    provider_name: str = field(default="mistral", init=False)
    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)

    async def startup(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=(self.base_url or DEFAULT_MISTRAL_BASE_URL).rstrip("/"),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Accept": "application/json",
                },
                timeout=httpx.Timeout(30.0),
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
        response = await client.post(
            "/v1/chat/completions",
            json=self._build_request_body(
                image_bytes=image_bytes,
                frame_context=frame_context,
                image_media_type=image_media_type,
            ),
        )
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
        payload = self._extract_provider_payload(response.json())
        return self._normalize_observation(payload=payload, frame_context=frame_context)

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
    ) -> dict[str, Any]:
        data_url = self._build_data_url(image_bytes=image_bytes, image_media_type=image_media_type)
        return {
            "model": self.model_name,
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
