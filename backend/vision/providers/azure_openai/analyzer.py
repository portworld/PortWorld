from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

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
    is_response_format_compatibility_error,
    normalize_observation,
    post_json_with_vision_errors,
    sanitize_sensitive_text,
    sanitize_url_for_logging,
)

DEFAULT_AZURE_OPENAI_API_VERSION = "2024-10-21"
_AZURE_DEPLOYMENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_AZURE_API_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

logger = logging.getLogger(__name__)


def _normalize_azure_endpoint(endpoint: str) -> str:
    candidate = endpoint.strip()
    if not candidate:
        raise RuntimeError(
            "VISION_AZURE_OPENAI_ENDPOINT is required when VISION_MEMORY_PROVIDER=azure_openai"
        )
    parsed = urlsplit(candidate)
    if parsed.scheme.lower() != "https":
        raise RuntimeError("VISION_AZURE_OPENAI_ENDPOINT must start with https://")
    if not parsed.netloc:
        raise RuntimeError(
            "VISION_AZURE_OPENAI_ENDPOINT must include a valid host when VISION_MEMORY_PROVIDER=azure_openai"
        )
    if parsed.username or parsed.password:
        raise RuntimeError(
            "VISION_AZURE_OPENAI_ENDPOINT must not include embedded credentials"
        )
    if parsed.query or parsed.fragment:
        raise RuntimeError(
            "VISION_AZURE_OPENAI_ENDPOINT must not include query or fragment components"
        )
    normalized_path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, normalized_path, "", ""))


def _normalize_azure_deployment(deployment: str) -> str:
    candidate = deployment.strip()
    if not candidate:
        raise RuntimeError(
            "VISION_AZURE_OPENAI_DEPLOYMENT or VISION_MEMORY_MODEL is required when VISION_MEMORY_PROVIDER=azure_openai"
        )
    if any(character.isspace() for character in candidate):
        raise RuntimeError("VISION_AZURE_OPENAI_DEPLOYMENT must not contain whitespace")
    if any(character in candidate for character in ["/", "\\", "?", "#", "&", "="]):
        raise RuntimeError(
            "VISION_AZURE_OPENAI_DEPLOYMENT contains unsupported delimiter characters"
        )
    if not _AZURE_DEPLOYMENT_PATTERN.fullmatch(candidate):
        raise RuntimeError(
            "VISION_AZURE_OPENAI_DEPLOYMENT contains unsupported characters"
        )
    return candidate


def _normalize_azure_api_version(api_version: str) -> str:
    candidate = api_version.strip()
    if not candidate:
        raise RuntimeError(
            "VISION_AZURE_OPENAI_API_VERSION cannot be empty when VISION_MEMORY_PROVIDER=azure_openai"
        )
    if any(character.isspace() for character in candidate):
        raise RuntimeError("VISION_AZURE_OPENAI_API_VERSION must not contain whitespace")
    if any(character in candidate for character in ["/", "\\", "?", "#", "&", "="]):
        raise RuntimeError(
            "VISION_AZURE_OPENAI_API_VERSION contains unsupported delimiter characters"
        )
    if not _AZURE_API_VERSION_PATTERN.fullmatch(candidate):
        raise RuntimeError(
            "VISION_AZURE_OPENAI_API_VERSION contains unsupported characters"
        )
    return candidate


def validate_azure_openai_vision_settings(settings: Settings) -> None:
    settings.validate_vision_provider_credentials(provider="azure_openai")

    endpoint = settings.resolve_vision_provider_endpoint(provider="azure_openai")
    if not endpoint:
        raise RuntimeError(
            "VISION_AZURE_OPENAI_ENDPOINT is required when VISION_MEMORY_PROVIDER=azure_openai"
        )
    _normalize_azure_endpoint(endpoint)

    deployment = settings.resolve_vision_provider_deployment(provider="azure_openai")
    if not deployment:
        raise RuntimeError(
            "VISION_AZURE_OPENAI_DEPLOYMENT or VISION_MEMORY_MODEL is required when VISION_MEMORY_PROVIDER=azure_openai"
        )
    _normalize_azure_deployment(deployment)

    api_version = settings.resolve_vision_provider_api_version(provider="azure_openai")
    if api_version:
        _normalize_azure_api_version(api_version)


def build_azure_openai_vision_analyzer(*, settings: Settings) -> "AzureOpenAIVisionAnalyzer":
    endpoint = settings.resolve_vision_provider_endpoint(provider="azure_openai")
    if endpoint is None:
        raise RuntimeError(
            "VISION_AZURE_OPENAI_ENDPOINT is required when VISION_MEMORY_PROVIDER=azure_openai"
        )

    deployment = settings.resolve_vision_provider_deployment(provider="azure_openai")
    if deployment is None:
        raise RuntimeError(
            "VISION_AZURE_OPENAI_DEPLOYMENT or VISION_MEMORY_MODEL is required when VISION_MEMORY_PROVIDER=azure_openai"
        )

    api_version = settings.resolve_vision_provider_api_version(provider="azure_openai")
    if api_version is None:
        api_version = DEFAULT_AZURE_OPENAI_API_VERSION

    return AzureOpenAIVisionAnalyzer(
        api_key=settings.require_vision_provider_api_key(provider="azure_openai"),
        deployment=_normalize_azure_deployment(deployment),
        endpoint=_normalize_azure_endpoint(endpoint),
        api_version=_normalize_azure_api_version(api_version),
    )


@dataclass(slots=True)
class AzureOpenAIVisionAnalyzer:
    api_key: str
    deployment: str
    endpoint: str
    api_version: str = DEFAULT_AZURE_OPENAI_API_VERSION
    model_name: str = field(init=False)
    provider_name: str = field(default="azure_openai", init=False)
    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)
    _supports_response_format: bool = field(default=True, init=False, repr=False)

    async def startup(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.endpoint.rstrip("/"),
                headers={
                    "api-key": self.api_key,
                    "Accept": "application/json",
                },
                timeout=httpx.Timeout(connect=8.0, read=20.0, write=20.0, pool=8.0),
            )

    def __post_init__(self) -> None:
        self.endpoint = _normalize_azure_endpoint(self.endpoint)
        self.deployment = _normalize_azure_deployment(self.deployment)
        self.api_version = _normalize_azure_api_version(self.api_version)
        self.model_name = self.deployment

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
            if self._supports_response_format and is_response_format_compatibility_error(exc):
                self._supports_response_format = False
                endpoint_for_log = sanitize_url_for_logging(self.endpoint.rstrip("/"))
                logger.warning(
                    "Vision provider rejected response_format; retrying without structured output provider=%s deployment=%s endpoint=%s provider_message=%s",
                    self.provider_name,
                    self.deployment,
                    endpoint_for_log,
                    (sanitize_sensitive_text(exc.provider_message) or "")[:220] or None,
                )
                response = await self._post_completion(
                    client=client,
                    request_body=self._build_request_body(
                        image_bytes=image_bytes,
                        frame_context=frame_context,
                        image_media_type=image_media_type,
                        include_response_format=False,
                    ),
                )
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
        deployment = quote(self.deployment, safe="")
        path = f"/openai/deployments/{deployment}/chat/completions"
        return await post_json_with_vision_errors(
            client=client,
            url=path,
            request_body=request_body,
            query_params={"api-version": self.api_version},
        )

    def _build_request_body(
        self,
        *,
        image_bytes: bytes,
        frame_context: VisionFrameContext,
        image_media_type: str,
        include_response_format: bool,
    ) -> dict[str, Any]:
        data_url = build_data_url(image_bytes=image_bytes, image_media_type=image_media_type)
        payload: dict[str, Any] = {
            "temperature": DEFAULT_VISION_TEMPERATURE,
            "top_p": DEFAULT_VISION_TOP_P,
            "max_tokens": DEFAULT_VISION_MAX_TOKENS,
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
        if include_response_format:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _extract_provider_payload(self, response_json: dict[str, Any]) -> ProviderObservationPayload:
        choices = response_json.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("Azure OpenAI response did not include choices")

        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise ValueError("Azure OpenAI response did not include a message payload")

        return parse_provider_observation_payload(coalesce_text_content(message.get("content")))
