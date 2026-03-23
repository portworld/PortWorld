from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import quote

import httpx

from backend.core.settings import Settings
from backend.core.storage import BackendStorage
from backend.memory.lifecycle import CROSS_SESSION_MEMORY_TEMPLATE, USER_MEMORY_TEMPLATE
from backend.vision.contracts import VisionProviderError
from backend.vision.providers.shared import (
    coalesce_text_content,
    is_response_format_compatibility_error,
    post_json_with_vision_errors,
    sanitize_sensitive_text,
)

logger = logging.getLogger(__name__)

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com"
DEFAULT_MISTRAL_BASE_URL = "https://api.mistral.ai"
DEFAULT_NVIDIA_INTEGRATE_BASE_URL = "https://integrate.api.nvidia.com"
DEFAULT_GROQ_BASE_URL = "https://api.groq.com"
DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"
DEFAULT_CLAUDE_BASE_URL = "https://api.anthropic.com"
DEFAULT_AZURE_OPENAI_API_VERSION = "2024-10-21"
DEFAULT_ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_CONSOLIDATION_TEMPERATURE = 0.1
DEFAULT_CONSOLIDATION_MAX_TOKENS = 1200

SYSTEM_PROMPT = """
You rewrite durable memory markdown for a realtime assistant.

Rules:
- Return exactly one JSON object.
- Rewrite complete replacement markdown documents for USER.md and CROSS_SESSION.md.
- Never append to prior memory. Always produce coherent refreshed documents.
- USER.md should keep only stable personal facts, preferences, intended use, and durable project/context bullets.
- Be conservative. If a candidate is uncertain, conflicting, low-confidence, or too transient, omit it.
- CROSS_SESSION.md should emphasize ongoing threads, follow-up items, and a small amount of recent factual recap.
- Do not include implementation details, tool names, or speculation.
- Preserve the expected document titles and section headings.
""".strip()


def _build_consolidation_user_prompt(
    *,
    session_id: str,
    current_user_memory: str,
    current_cross_session_memory: str,
    session_memory_markdown: str,
    memory_candidates: list[dict[str, Any]],
) -> str:
    payload = json.dumps(
        {
            "session_id": session_id,
            "current_user_memory_markdown": current_user_memory or USER_MEMORY_TEMPLATE,
            "current_cross_session_memory_markdown": (
                current_cross_session_memory or CROSS_SESSION_MEMORY_TEMPLATE
            ),
            "session_long_term_memory_markdown": session_memory_markdown,
            "memory_candidates": memory_candidates,
            "required_user_memory_sections": [
                "Identity",
                "Preferences",
                "Stable Facts",
                "Open Questions",
            ],
            "required_cross_session_sections": [
                "Active Themes",
                "Ongoing Projects",
                "Important Recent Facts",
                "Follow-Up Items",
            ],
        },
        ensure_ascii=False,
        indent=2,
    )
    return (
        "Rewrite USER.md and CROSS_SESSION.md from this state.\n"
        "Return JSON with keys user_memory_markdown and cross_session_memory_markdown.\n\n"
        f"{payload}"
    )


def _parse_json_object(raw_text: str) -> dict[str, Any]:
    candidate = raw_text.strip()
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            raise
        payload = json.loads(candidate[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("Provider response was not a JSON object")
    return payload


class DurableMemoryConsolidationClient:
    def __init__(self, *, settings: Settings) -> None:
        self.settings = settings
        self.provider_name = settings.resolve_memory_consolidation_provider()
        self.model_name = settings.resolve_memory_consolidation_model() or ""
        self._http_client: httpx.AsyncClient | None = None
        self._bedrock_client: Any | None = None
        self._supports_response_format: bool = True

    async def startup(self) -> None:
        timeout = httpx.Timeout(
            max(1.0, self.settings.memory_consolidation_timeout_ms / 1000.0)
        )

        if self.provider_name in {"openai", "mistral", "nvidia_integrate", "groq"}:
            base_url = {
                "openai": self.settings.resolve_vision_provider_base_url(provider="openai")
                or DEFAULT_OPENAI_BASE_URL,
                "mistral": self.settings.resolve_vision_provider_base_url(provider="mistral")
                or DEFAULT_MISTRAL_BASE_URL,
                "nvidia_integrate": self.settings.resolve_vision_provider_base_url(
                    provider="nvidia_integrate"
                )
                or DEFAULT_NVIDIA_INTEGRATE_BASE_URL,
                "groq": self.settings.resolve_vision_provider_base_url(provider="groq")
                or DEFAULT_GROQ_BASE_URL,
            }[self.provider_name]
            api_key = self.settings.require_vision_provider_api_key(provider=self.provider_name)
            self._http_client = httpx.AsyncClient(
                base_url=base_url.rstrip("/"),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "application/json",
                },
                timeout=timeout,
            )
            return

        if self.provider_name == "azure_openai":
            endpoint = self.settings.resolve_vision_provider_endpoint(provider="azure_openai")
            if not endpoint:
                raise RuntimeError(
                    "VISION_AZURE_OPENAI_ENDPOINT is required when "
                    "MEMORY_CONSOLIDATION_ENABLED=true and VISION_MEMORY_PROVIDER=azure_openai"
                )
            self._http_client = httpx.AsyncClient(
                base_url=endpoint.rstrip("/"),
                headers={
                    "api-key": self.settings.require_vision_provider_api_key(
                        provider="azure_openai"
                    ),
                    "Accept": "application/json",
                },
                timeout=timeout,
            )
            return

        if self.provider_name == "gemini":
            base_url = (
                self.settings.resolve_vision_provider_base_url(provider="gemini")
                or DEFAULT_GEMINI_BASE_URL
            )
            self._http_client = httpx.AsyncClient(
                base_url=base_url.rstrip("/"),
                headers={
                    "x-goog-api-key": self.settings.require_vision_provider_api_key(
                        provider="gemini"
                    ),
                    "Accept": "application/json",
                },
                timeout=timeout,
            )
            return

        if self.provider_name == "claude":
            base_url = (
                self.settings.resolve_vision_provider_base_url(provider="claude")
                or DEFAULT_CLAUDE_BASE_URL
            )
            self._http_client = httpx.AsyncClient(
                base_url=base_url.rstrip("/"),
                headers={
                    "x-api-key": self.settings.require_vision_provider_api_key(provider="claude"),
                    "anthropic-version": DEFAULT_ANTHROPIC_VERSION,
                    "content-type": "application/json",
                    "accept": "application/json",
                },
                timeout=timeout,
            )
            return

        if self.provider_name == "bedrock":
            self._bedrock_client = await asyncio.to_thread(self._build_bedrock_client)
            return

        raise RuntimeError(
            f"Unsupported memory consolidation provider={self.provider_name!r}"
        )

    async def shutdown(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None
        self._bedrock_client = None

    async def request_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        if self.provider_name in {"openai", "mistral", "nvidia_integrate", "groq"}:
            return await self._request_openai_compatible_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
        if self.provider_name == "azure_openai":
            return await self._request_azure_openai_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
        if self.provider_name == "gemini":
            return await self._request_gemini_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
        if self.provider_name == "claude":
            return await self._request_claude_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
        if self.provider_name == "bedrock":
            return await self._request_bedrock_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
        raise RuntimeError(
            f"Unsupported memory consolidation provider={self.provider_name!r}"
        )

    def _build_bedrock_client(self) -> Any:
        try:
            import boto3
            from botocore.config import Config as BotocoreConfig
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "boto3 and botocore are required when MEMORY_CONSOLIDATION_ENABLED=true "
                "and VISION_MEMORY_PROVIDER=bedrock"
            ) from exc

        client_kwargs: dict[str, Any] = {
            "service_name": "bedrock-runtime",
            "region_name": self.settings.resolve_vision_provider_region(provider="bedrock"),
            "config": BotocoreConfig(
                connect_timeout=8,
                read_timeout=max(
                    12,
                    int(self.settings.memory_consolidation_timeout_ms / 1000),
                ),
                retries={"mode": "standard", "max_attempts": 5},
            ),
        }

        access_key_id = self.settings.resolve_vision_provider_aws_access_key_id(
            provider="bedrock"
        )
        secret_access_key = self.settings.resolve_vision_provider_aws_secret_access_key(
            provider="bedrock"
        )
        session_token = self.settings.resolve_vision_provider_aws_session_token(
            provider="bedrock"
        )
        if access_key_id:
            client_kwargs["aws_access_key_id"] = access_key_id
        if secret_access_key:
            client_kwargs["aws_secret_access_key"] = secret_access_key
        if session_token:
            client_kwargs["aws_session_token"] = session_token

        session = boto3.session.Session()
        return session.client(**client_kwargs)

    async def _ensure_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            await self.startup()
        assert self._http_client is not None
        return self._http_client

    async def _request_openai_compatible_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        client = await self._ensure_http_client()
        path = "/v1/chat/completions"
        if self.provider_name == "groq":
            path = "/openai/v1/chat/completions"

        include_response_format = self._supports_response_format
        while True:
            request_body: dict[str, Any] = {
                "model": self.model_name,
                "temperature": DEFAULT_CONSOLIDATION_TEMPERATURE,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
            if include_response_format:
                request_body["response_format"] = {"type": "json_object"}
            try:
                response = await post_json_with_vision_errors(
                    client=client,
                    url=path,
                    request_body=request_body,
                )
                response_json = response.json()
                return _parse_json_object(
                    coalesce_text_content(
                        response_json["choices"][0]["message"]["content"]
                    )
                )
            except VisionProviderError as exc:
                if include_response_format and is_response_format_compatibility_error(exc):
                    include_response_format = False
                    self._supports_response_format = False
                    continue
                raise

    async def _request_azure_openai_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        client = await self._ensure_http_client()
        deployment = self.settings.resolve_vision_provider_deployment(
            provider="azure_openai"
        )
        if not deployment:
            raise RuntimeError(
                "VISION_AZURE_OPENAI_DEPLOYMENT or VISION_AZURE_OPENAI_MODEL is required when "
                "MEMORY_CONSOLIDATION_ENABLED=true and VISION_MEMORY_PROVIDER=azure_openai"
            )
        api_version = (
            self.settings.resolve_vision_provider_api_version(provider="azure_openai")
            or DEFAULT_AZURE_OPENAI_API_VERSION
        )
        path = f"/openai/deployments/{quote(deployment, safe='')}/chat/completions"

        include_response_format = self._supports_response_format
        while True:
            request_body: dict[str, Any] = {
                "temperature": DEFAULT_CONSOLIDATION_TEMPERATURE,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
            if include_response_format:
                request_body["response_format"] = {"type": "json_object"}
            try:
                response = await post_json_with_vision_errors(
                    client=client,
                    url=path,
                    request_body=request_body,
                    query_params={"api-version": api_version},
                )
                response_json = response.json()
                return _parse_json_object(
                    coalesce_text_content(
                        response_json["choices"][0]["message"]["content"]
                    )
                )
            except VisionProviderError as exc:
                if include_response_format and is_response_format_compatibility_error(exc):
                    include_response_format = False
                    self._supports_response_format = False
                    continue
                raise

    async def _request_gemini_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        client = await self._ensure_http_client()
        model = quote(self.model_name.strip(), safe="")
        response = await post_json_with_vision_errors(
            client=client,
            url=f"/v1beta/models/{model}:generateContent",
            request_body={
                "system_instruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "generationConfig": {
                    "temperature": DEFAULT_CONSOLIDATION_TEMPERATURE,
                    "maxOutputTokens": DEFAULT_CONSOLIDATION_MAX_TOKENS,
                    "responseMimeType": "application/json",
                },
            },
        )
        response_json = response.json()
        candidates = response_json.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ValueError("Gemini consolidation response did not include candidates")
        content = candidates[0].get("content")
        if not isinstance(content, dict):
            raise ValueError("Gemini consolidation response did not include content")
        parts = content.get("parts")
        if not isinstance(parts, list) or not parts:
            raise ValueError("Gemini consolidation response did not include parts")
        text_parts = [
            part.get("text")
            for part in parts
            if isinstance(part, dict)
            and isinstance(part.get("text"), str)
            and part.get("text").strip()
        ]
        if not text_parts:
            raise ValueError("Gemini consolidation response did not include text")
        return _parse_json_object("\n".join(text_parts))

    async def _request_claude_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        client = await self._ensure_http_client()
        response = await post_json_with_vision_errors(
            client=client,
            url="/v1/messages",
            request_body={
                "model": self.model_name,
                "system": system_prompt,
                "max_tokens": DEFAULT_CONSOLIDATION_MAX_TOKENS,
                "temperature": DEFAULT_CONSOLIDATION_TEMPERATURE,
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": user_prompt}],
                    }
                ],
            },
        )
        response_json = response.json()
        content = response_json.get("content")
        if not isinstance(content, list) or not content:
            raise ValueError("Claude consolidation response did not include content")
        text_parts = [
            item.get("text")
            for item in content
            if isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
            and item.get("text").strip()
        ]
        if not text_parts:
            raise ValueError("Claude consolidation response did not include text")
        return _parse_json_object("\n".join(text_parts))

    async def _request_bedrock_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        if self._bedrock_client is None:
            await self.startup()
        assert self._bedrock_client is not None
        try:
            response = await asyncio.to_thread(
                self._bedrock_client.converse,
                modelId=self.model_name,
                system=[{"text": system_prompt}],
                messages=[
                    {
                        "role": "user",
                        "content": [{"text": user_prompt}],
                    }
                ],
                inferenceConfig={
                    "maxTokens": DEFAULT_CONSOLIDATION_MAX_TOKENS,
                    "temperature": DEFAULT_CONSOLIDATION_TEMPERATURE,
                    "topP": 0.1,
                },
            )
        except Exception as exc:  # pragma: no cover - optional SDK/runtime
            raise RuntimeError(
                sanitize_sensitive_text(str(exc)) or "Bedrock request failed"
            ) from exc

        output = response.get("output")
        if not isinstance(output, dict):
            raise ValueError("Bedrock consolidation response did not include output")
        message = output.get("message")
        if not isinstance(message, dict):
            raise ValueError("Bedrock consolidation response did not include message")
        content = message.get("content")
        if not isinstance(content, list) or not content:
            raise ValueError("Bedrock consolidation response did not include content")
        text_parts = [
            item.get("text")
            for item in content
            if isinstance(item, dict)
            and isinstance(item.get("text"), str)
            and item.get("text").strip()
        ]
        if not text_parts:
            raise ValueError("Bedrock consolidation response did not include text")
        return _parse_json_object("\n".join(text_parts))


class DurableMemoryConsolidationRuntime:
    def __init__(self, *, settings: Settings, storage: BackendStorage) -> None:
        self.settings = settings
        self.storage = storage
        self._client = DurableMemoryConsolidationClient(settings=settings)

    async def startup(self) -> None:
        await self._client.startup()

    async def shutdown(self) -> None:
        await self._client.shutdown()

    async def finalize_session(self, *, session_id: str) -> None:
        if not self.settings.memory_consolidation_enabled:
            return

        candidates = await _run_storage(self.storage.read_memory_candidates, session_id=session_id)
        session_memory_payload = await _run_storage(
            self.storage.read_session_memory,
            session_id=session_id,
        )
        session_memory_markdown = await _run_storage(
            self.storage.read_session_memory_markdown,
            session_id=session_id,
        )
        if not candidates and not session_memory_payload:
            return

        user_memory_markdown = await _run_storage(self.storage.read_user_memory)
        cross_session_markdown = await _run_storage(self.storage.read_cross_session_memory)

        payload = await self._request_consolidation(
            session_id=session_id,
            current_user_memory=user_memory_markdown,
            current_cross_session_memory=cross_session_markdown,
            session_memory_markdown=session_memory_markdown,
            memory_candidates=candidates,
        )
        if payload is None:
            return

        next_user_memory = _normalize_markdown_document(
            payload.get("user_memory_markdown"),
            fallback=USER_MEMORY_TEMPLATE,
            expected_header="# User",
        )
        next_cross_session = _normalize_markdown_document(
            payload.get("cross_session_memory_markdown"),
            fallback=CROSS_SESSION_MEMORY_TEMPLATE,
            expected_header="# Cross-Session Memory",
        )
        if next_user_memory is None or next_cross_session is None:
            logger.warning("Memory consolidation produced invalid markdown session=%s", session_id)
            return

        if _materially_changed(user_memory_markdown, next_user_memory):
            await _run_storage(self.storage.write_user_memory, markdown=next_user_memory)
        if _materially_changed(cross_session_markdown, next_cross_session):
            await _run_storage(self.storage.write_cross_session_memory, markdown=next_cross_session)

    async def _request_consolidation(
        self,
        *,
        session_id: str,
        current_user_memory: str,
        current_cross_session_memory: str,
        session_memory_markdown: str,
        memory_candidates: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        try:
            return await self._client.request_json(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=_build_consolidation_user_prompt(
                    session_id=session_id,
                    current_user_memory=current_user_memory,
                    current_cross_session_memory=current_cross_session_memory,
                    session_memory_markdown=session_memory_markdown,
                    memory_candidates=memory_candidates,
                ),
            )
        except (VisionProviderError, RuntimeError, ValueError, KeyError, httpx.HTTPError) as exc:
            logger.warning(
                "Memory consolidation request failed session=%s provider=%s detail=%s",
                session_id,
                self._client.provider_name,
                sanitize_sensitive_text(str(exc)),
            )
            return None


async def _run_storage(function, /, *args, **kwargs):
    return await asyncio.to_thread(function, *args, **kwargs)


def _normalize_markdown_document(
    raw: object,
    *,
    fallback: str,
    expected_header: str,
) -> str | None:
    if not isinstance(raw, str):
        return None
    candidate = raw.strip()
    if not candidate:
        candidate = fallback.strip()
    if not candidate.startswith(expected_header):
        return None
    return candidate + "\n"


def _materially_changed(current: str, candidate: str) -> bool:
    return current.strip() != candidate.strip()
