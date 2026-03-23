from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping

import httpx
from pydantic import ValidationError

from backend.vision.contracts import (
    ProviderObservationPayload,
    VisionFrameContext,
    VisionObservation,
    VisionProviderError,
)
from backend.vision.providers.shared import (
    extract_provider_content_excerpt_from_chat_choices,
    is_max_completion_tokens_compatibility_error,
    is_response_format_compatibility_error,
    normalize_observation,
    post_json_with_vision_errors,
    sanitize_sensitive_text,
    sanitize_url_for_logging,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OpenAICompatibleVisionAnalyzerBase(ABC):
    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)
    _supports_response_format: bool = field(default=True, init=False, repr=False)
    _uses_legacy_max_tokens: bool = field(default=False, init=False, repr=False)

    @property
    @abstractmethod
    def default_base_url(self) -> str: ...

    @property
    def enable_response_format_fallback(self) -> bool:
        return True

    @property
    def enable_legacy_max_tokens_fallback(self) -> bool:
        return True

    @property
    def base_url_log_label(self) -> str:
        return "base_url"

    @property
    def target_log_label(self) -> str:
        return "model"

    @property
    def target_log_value(self) -> str:
        return getattr(self, "model_name")

    @abstractmethod
    def build_client_headers(self) -> Mapping[str, str]: ...

    @abstractmethod
    async def post_completion(
        self,
        *,
        client: httpx.AsyncClient,
        request_body: dict[str, Any],
    ) -> httpx.Response: ...

    @abstractmethod
    def build_request_body(
        self,
        *,
        image_bytes: bytes,
        frame_context: VisionFrameContext,
        image_media_type: str,
        include_response_format: bool,
        use_legacy_max_tokens: bool,
    ) -> dict[str, Any]: ...

    @abstractmethod
    def extract_provider_payload(self, response_json: dict[str, Any]) -> ProviderObservationPayload: ...

    # Backward-compatible private aliases used by existing smoke checks and internal tooling.
    def _build_request_body(
        self,
        *,
        image_bytes: bytes,
        frame_context: VisionFrameContext,
        image_media_type: str,
        include_response_format: bool,
        use_legacy_max_tokens: bool,
    ) -> dict[str, Any]:
        return self.build_request_body(
            image_bytes=image_bytes,
            frame_context=frame_context,
            image_media_type=image_media_type,
            include_response_format=include_response_format,
            use_legacy_max_tokens=use_legacy_max_tokens,
        )

    def _extract_provider_payload(self, response_json: dict[str, Any]) -> ProviderObservationPayload:
        return self.extract_provider_payload(response_json)

    def extract_payload_excerpt(self, response_json: dict[str, Any]) -> str | None:
        return extract_provider_content_excerpt_from_chat_choices(response_json)

    def _resolved_base_url(self) -> str:
        base_url = getattr(self, "base_url", None)
        return (base_url or self.default_base_url).rstrip("/")

    async def startup(self) -> None:
        if self._client is not None:
            return
        self._client = httpx.AsyncClient(
            base_url=self._resolved_base_url(),
            headers=dict(self.build_client_headers()),
            timeout=httpx.Timeout(connect=8.0, read=20.0, write=20.0, pool=8.0),
        )

    async def shutdown(self) -> None:
        if self._client is None:
            return
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
        request_body = self.build_request_body(
            image_bytes=image_bytes,
            frame_context=frame_context,
            image_media_type=image_media_type,
            include_response_format=self._supports_response_format,
            use_legacy_max_tokens=self._uses_legacy_max_tokens,
        )

        while True:
            try:
                response = await self.post_completion(client=client, request_body=request_body)
                break
            except VisionProviderError as exc:
                base_url_for_log = sanitize_url_for_logging(self._resolved_base_url())
                provider_message_excerpt = (sanitize_sensitive_text(exc.provider_message) or "")[:220] or None

                if (
                    self.enable_legacy_max_tokens_fallback
                    and not self._uses_legacy_max_tokens
                    and is_max_completion_tokens_compatibility_error(exc)
                ):
                    self._uses_legacy_max_tokens = True
                    logger.warning(
                        "Vision provider rejected max_completion_tokens; retrying with legacy max_tokens provider=%s %s=%s %s=%s provider_message=%s",
                        self.provider_name,
                        self.target_log_label,
                        self.target_log_value,
                        self.base_url_log_label,
                        base_url_for_log,
                        provider_message_excerpt,
                    )
                    request_body = self.build_request_body(
                        image_bytes=image_bytes,
                        frame_context=frame_context,
                        image_media_type=image_media_type,
                        include_response_format=self._supports_response_format,
                        use_legacy_max_tokens=True,
                    )
                    continue

                if (
                    self.enable_response_format_fallback
                    and self._supports_response_format
                    and is_response_format_compatibility_error(exc)
                ):
                    self._supports_response_format = False
                    logger.warning(
                        "Vision provider rejected response_format; retrying without structured output provider=%s %s=%s %s=%s provider_message=%s",
                        self.provider_name,
                        self.target_log_label,
                        self.target_log_value,
                        self.base_url_log_label,
                        base_url_for_log,
                        provider_message_excerpt,
                    )
                    request_body = self.build_request_body(
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
            payload = self.extract_provider_payload(response_json)
        except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
            raise VisionProviderError(
                status_code=response.status_code,
                provider_error_code="provider_payload_invalid_json",
                provider_message="Vision provider returned an observation payload that could not be parsed",
                payload_excerpt=self.extract_payload_excerpt(response_json),
            ) from exc

        return normalize_observation(payload=payload, frame_context=frame_context)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            await self.startup()
        assert self._client is not None
        return self._client


async def post_openai_compatible_completion(
    *,
    client: httpx.AsyncClient,
    url: str,
    request_body: Mapping[str, Any],
    query_params: Mapping[str, Any] | None = None,
) -> httpx.Response:
    return await post_json_with_vision_errors(
        client=client,
        url=url,
        request_body=request_body,
        query_params=query_params,
    )
