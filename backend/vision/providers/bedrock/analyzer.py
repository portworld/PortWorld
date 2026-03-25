from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

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
from backend.vision.providers.shared import (
    DEFAULT_VISION_MAX_TOKENS,
    DEFAULT_VISION_TEMPERATURE,
    DEFAULT_VISION_TOP_P,
    build_provider_payload_parse_error,
    VISION_SYSTEM_PROMPT,
    build_user_prompt,
    normalize_observation,
    safe_json_excerpt,
)

BEDROCK_CONNECT_TIMEOUT_SECONDS = 8
BEDROCK_READ_TIMEOUT_SECONDS = 25
BEDROCK_MAX_RETRY_ATTEMPTS = 5


def validate_bedrock_vision_settings(settings: Settings) -> None:
    region = settings.resolve_vision_provider_region(provider="bedrock")
    if not region:
        raise RuntimeError(
            "VISION_BEDROCK_REGION is required when VISION_MEMORY_PROVIDER=bedrock"
        )

    model_name = (settings.resolve_vision_provider_model(provider="bedrock") or "").strip()
    if not model_name:
        raise RuntimeError(
            "VISION_BEDROCK_MODEL is required when VISION_MEMORY_PROVIDER=bedrock"
        )


def build_bedrock_vision_analyzer(*, settings: Settings) -> "BedrockVisionAnalyzer":
    region = settings.resolve_vision_provider_region(provider="bedrock")
    if region is None:
        raise RuntimeError(
            "VISION_BEDROCK_REGION is required when VISION_MEMORY_PROVIDER=bedrock"
        )

    return BedrockVisionAnalyzer(
        model_name=settings.resolve_vision_provider_model(provider="bedrock") or "",
        region_name=region,
        aws_access_key_id=settings.resolve_vision_provider_aws_access_key_id(provider="bedrock"),
        aws_secret_access_key=settings.resolve_vision_provider_aws_secret_access_key(
            provider="bedrock"
        ),
        aws_session_token=settings.resolve_vision_provider_aws_session_token(provider="bedrock"),
    )


@dataclass(slots=True)
class BedrockVisionAnalyzer:
    model_name: str
    region_name: str
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_session_token: str | None = None
    provider_name: str = field(default="bedrock", init=False)
    _client: Any | None = field(default=None, init=False, repr=False)

    async def startup(self) -> None:
        if self._client is not None:
            return
        try:
            import boto3
            from botocore.config import Config as BotocoreConfig
        except ImportError as exc:
            raise RuntimeError(
                "boto3 and botocore are required when VISION_MEMORY_PROVIDER=bedrock"
            ) from exc

        client_kwargs: dict[str, Any] = {
            "service_name": "bedrock-runtime",
            "region_name": self.region_name,
            "config": BotocoreConfig(
                connect_timeout=BEDROCK_CONNECT_TIMEOUT_SECONDS,
                read_timeout=BEDROCK_READ_TIMEOUT_SECONDS,
                retries={
                    "mode": "standard",
                    "max_attempts": BEDROCK_MAX_RETRY_ATTEMPTS,
                },
            ),
        }
        if self.aws_access_key_id:
            client_kwargs["aws_access_key_id"] = self.aws_access_key_id
        if self.aws_secret_access_key:
            client_kwargs["aws_secret_access_key"] = self.aws_secret_access_key
        if self.aws_session_token:
            client_kwargs["aws_session_token"] = self.aws_session_token

        session = boto3.session.Session()
        self._client = session.client(**client_kwargs)

    async def shutdown(self) -> None:
        self._client = None

    async def analyze_frame(
        self,
        *,
        image_bytes: bytes,
        frame_context: VisionFrameContext,
        image_media_type: str = "image/jpeg",
    ) -> VisionObservation:
        client = await self._get_client()
        try:
            response = await asyncio.to_thread(
                client.converse,
                modelId=self.model_name,
                system=[{"text": VISION_SYSTEM_PROMPT}],
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"text": build_user_prompt(frame_context=frame_context)},
                            {
                                "image": {
                                    "format": _image_format_from_media_type(image_media_type),
                                    "source": {"bytes": image_bytes},
                                }
                            },
                        ],
                    }
                ],
                inferenceConfig={
                    "maxTokens": DEFAULT_VISION_MAX_TOKENS,
                    "temperature": DEFAULT_VISION_TEMPERATURE,
                    "topP": DEFAULT_VISION_TOP_P,
                },
            )
        except Exception as exc:  # pragma: no cover - depends on optional botocore install
            raise _map_bedrock_exception(exc) from exc

        try:
            payload = self._extract_provider_payload(response)
        except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
            payload_text: str | None = None
            if isinstance(response, dict):
                output = response.get("output")
                if isinstance(output, dict):
                    message = output.get("message")
                    if isinstance(message, dict):
                        content = message.get("content")
                        if isinstance(content, list):
                            text_parts = [
                                item.get("text")
                                for item in content
                                if isinstance(item, dict) and isinstance(item.get("text"), str)
                            ]
                            payload_text = "\n".join(text_parts) if text_parts else None
            raise build_provider_payload_parse_error(
                status_code=None,
                payload_text=payload_text,
                payload_excerpt=safe_json_excerpt(response),
            ) from exc

        return normalize_observation(payload=payload, frame_context=frame_context)

    async def _get_client(self) -> Any:
        if self._client is None:
            await self.startup()
        assert self._client is not None
        return self._client

    def _extract_provider_payload(
        self,
        response_json: dict[str, Any],
        *,
        status_code: int | None = None,
    ) -> ProviderObservationPayload:
        output = response_json.get("output")
        if not isinstance(output, dict):
            raise ValueError("Bedrock response did not include output")

        message = output.get("message")
        if not isinstance(message, dict):
            raise ValueError("Bedrock response did not include message")

        content = message.get("content")
        if not isinstance(content, list) or not content:
            raise ValueError("Bedrock response did not include content")

        text_parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text_value = item.get("text")
            if isinstance(text_value, str) and text_value.strip():
                text_parts.append(text_value)

        if not text_parts:
            raise ValueError("Bedrock response did not include text content")

        payload_text = "\n".join(text_parts)
        try:
            return parse_provider_observation_payload(payload_text)
        except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
            raise build_provider_payload_parse_error(
                status_code=status_code,
                payload_text=payload_text,
                payload_excerpt=safe_json_excerpt(response_json),
            ) from exc


def _image_format_from_media_type(media_type: str) -> str:
    normalized = media_type.strip().lower()
    if "/" not in normalized:
        return "jpeg"
    format_name = normalized.split("/", 1)[1]
    if format_name == "jpg":
        return "jpeg"
    return format_name or "jpeg"


def _map_bedrock_exception(exc: Exception) -> Exception:
    code = ""
    message = str(exc)
    status_code: int | None = None

    response_payload = getattr(exc, "response", None)
    if isinstance(response_payload, dict):
        error_payload = response_payload.get("Error")
        if isinstance(error_payload, dict):
            raw_code = error_payload.get("Code")
            if isinstance(raw_code, str):
                code = raw_code
            raw_message = error_payload.get("Message")
            if isinstance(raw_message, str) and raw_message.strip():
                message = raw_message
        metadata = response_payload.get("ResponseMetadata")
        if isinstance(metadata, dict):
            candidate_status = metadata.get("HTTPStatusCode")
            if isinstance(candidate_status, int):
                status_code = candidate_status

    lowered = code.lower()
    if "throttl" in lowered or status_code == 429:
        return VisionRateLimitError(
            status_code=429,
            provider_error_code=code or "ThrottlingException",
            provider_message=message,
            payload_excerpt=safe_json_excerpt(response_payload),
        )

    return VisionProviderError(
        status_code=status_code,
        provider_error_code=code or "bedrock_request_failed",
        provider_message=message,
        payload_excerpt=safe_json_excerpt(response_payload),
    )
