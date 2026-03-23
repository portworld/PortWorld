from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

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
    VISION_SYSTEM_PROMPT,
    build_data_url,
    build_user_prompt,
    coalesce_text_content,
)

DEFAULT_AZURE_OPENAI_API_VERSION = "2024-10-21"
_AZURE_DEPLOYMENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_AZURE_API_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


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
            "VISION_AZURE_OPENAI_DEPLOYMENT or VISION_AZURE_OPENAI_MODEL is required "
            "when VISION_MEMORY_PROVIDER=azure_openai"
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
            "VISION_AZURE_OPENAI_DEPLOYMENT or VISION_AZURE_OPENAI_MODEL is required "
            "when VISION_MEMORY_PROVIDER=azure_openai"
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
            "VISION_AZURE_OPENAI_DEPLOYMENT or VISION_AZURE_OPENAI_MODEL is required "
            "when VISION_MEMORY_PROVIDER=azure_openai"
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
class AzureOpenAIVisionAnalyzer(OpenAICompatibleVisionAnalyzerBase):
    api_key: str
    deployment: str
    endpoint: str
    api_version: str = DEFAULT_AZURE_OPENAI_API_VERSION
    model_name: str = field(init=False)
    provider_name: str = field(default="azure_openai", init=False)
    base_url: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.endpoint = _normalize_azure_endpoint(self.endpoint)
        self.deployment = _normalize_azure_deployment(self.deployment)
        self.api_version = _normalize_azure_api_version(self.api_version)
        self.model_name = self.deployment
        self.base_url = self.endpoint

    @property
    def default_base_url(self) -> str:
        return self.endpoint

    @property
    def base_url_log_label(self) -> str:
        return "endpoint"

    @property
    def target_log_label(self) -> str:
        return "deployment"

    @property
    def target_log_value(self) -> str:
        return self.deployment

    def build_client_headers(self) -> Mapping[str, str]:
        return {
            "api-key": self.api_key,
            "Accept": "application/json",
        }

    async def post_completion(
        self,
        *,
        client: httpx.AsyncClient,
        request_body: dict[str, Any],
    ) -> httpx.Response:
        deployment = quote(self.deployment, safe="")
        path = f"/openai/deployments/{deployment}/chat/completions"
        return await post_openai_compatible_completion(
            client=client,
            url=path,
            request_body=request_body,
            query_params={"api-version": self.api_version},
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

    def extract_provider_payload(self, response_json: dict[str, Any]) -> ProviderObservationPayload:
        choices = response_json.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("Azure OpenAI response did not include choices")

        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise ValueError("Azure OpenAI response did not include a message payload")

        return parse_provider_observation_payload(coalesce_text_content(message.get("content")))
