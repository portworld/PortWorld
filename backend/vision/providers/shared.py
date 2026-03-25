from __future__ import annotations

import base64
import datetime as dt
import email.utils
import json
import re
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

import httpx

from backend.vision.contracts import (
    ProviderObservationPayload,
    VisionFrameContext,
    VisionObservation,
    VisionProviderError,
    VisionRateLimitError,
)

DEFAULT_VISION_TEMPERATURE = 0.0
DEFAULT_VISION_TOP_P = 0.1
DEFAULT_VISION_MAX_TOKENS = 280

VISION_SYSTEM_PROMPT = (
    "You are a vision observation service for a realtime wearable assistant. "
    "Return exactly one compact JSON object with keys: "
    "scene_summary, user_activity_guess, entities, actions, visible_text, documents_seen, salient_change, confidence. "
    "Do not include markdown, code fences, or extra commentary. "
    "Keep scene_summary short and factual. "
    "Set user_activity_guess to a single short string. "
    "Use arrays of short strings for entities, actions, visible_text, and documents_seen. "
    "Set salient_change to the JSON boolean true or false. "
    "Set confidence as a JSON number between 0.0 and 1.0."
)

_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_AUTHORIZATION_PATTERN = re.compile(r"(?i)\b(authorization\s*[:=]\s*)([^\s,;]+)")
_BEARER_TOKEN_PATTERN = re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9._~+/=-]+)")
_SENSITIVE_KEY_VALUE_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|sig|signature)\b\s*[:=]\s*([^\s,;]+)"
)


def sanitize_url_for_logging(url: str | None) -> str | None:
    if url is None:
        return None
    candidate = url.strip()
    if not candidate:
        return None
    try:
        parsed = urlsplit(candidate)
    except Exception:
        return "<redacted-url>"

    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        return "<redacted-url>"

    hostname = parsed.hostname or ""
    if not hostname:
        return "<redacted-url>"

    port = parsed.port
    netloc = f"{hostname}:{port}" if port is not None else hostname
    path = parsed.path or ""
    sanitized = urlunsplit((scheme, netloc, path, "", ""))
    if parsed.query:
        sanitized = f"{sanitized}?<redacted>"
    return sanitized


def _sanitize_url_match(match: re.Match[str]) -> str:
    return sanitize_url_for_logging(match.group(0)) or "<redacted-url>"


def sanitize_sensitive_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None

    sanitized = _URL_PATTERN.sub(_sanitize_url_match, text)
    sanitized = _AUTHORIZATION_PATTERN.sub(r"\1<redacted>", sanitized)
    sanitized = _BEARER_TOKEN_PATTERN.sub(r"\1<redacted>", sanitized)
    sanitized = _SENSITIVE_KEY_VALUE_PATTERN.sub(r"\1=<redacted>", sanitized)
    return sanitized


def build_data_url(*, image_bytes: bytes, image_media_type: str) -> str:
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{image_media_type};base64,{encoded}"


def build_base64_data(*, image_bytes: bytes) -> str:
    return base64.b64encode(image_bytes).decode("ascii")


def build_user_prompt(*, frame_context: VisionFrameContext) -> str:
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


def normalize_observation(
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


def coalesce_text_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise ValueError("Provider response content had an unsupported shape")

    text_parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            text_parts.append(item)
            continue
        if not isinstance(item, dict):
            continue
        item_type = (item.get("type") or "").strip().lower()
        if item_type in {"text", "output_text"}:
            text_value = item.get("text")
            if isinstance(text_value, str):
                text_parts.append(text_value)
                continue
        if item_type == "text_delta":
            text_value = item.get("delta")
            if isinstance(text_value, str):
                text_parts.append(text_value)
                continue
    if not text_parts:
        raise ValueError("Provider response content list did not contain text")
    return "\n".join(text_parts)


def is_likely_truncated_json_payload(payload_text: str | None) -> bool:
    if payload_text is None:
        return False

    text = payload_text.strip()
    if not text:
        return False

    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[0].startswith("```"):
            text = "\n".join(lines[1:-1]).strip() if lines[-1].startswith("```") else "\n".join(lines[1:]).strip()
        if text.lower().startswith("json\n"):
            text = text[5:].strip()

    if text.count("{") > text.count("}"):
        return True
    if text.count("[") > text.count("]"):
        return True

    tail = text.rstrip()
    if not tail:
        return False
    if tail.endswith(("...", ",", ":", "{", "[")):
        return True
    return False


def build_provider_payload_parse_error(
    *,
    status_code: int | None,
    payload_text: str | None,
    payload_excerpt: str | None,
) -> VisionProviderError:
    excerpt = sanitize_sensitive_text(payload_excerpt or payload_text)
    if excerpt is not None:
        excerpt = excerpt[:400]

    if is_likely_truncated_json_payload(payload_text):
        return VisionProviderError(
            status_code=status_code,
            provider_error_code="provider_payload_truncated_json",
            provider_message="Vision provider returned a truncated observation payload",
            payload_excerpt=excerpt,
        )

    return VisionProviderError(
        status_code=status_code,
        provider_error_code="provider_payload_invalid_json",
        provider_message="Vision provider returned an observation payload that could not be parsed",
        payload_excerpt=excerpt,
    )


def extract_provider_content_excerpt_from_chat_choices(response_json: Mapping[str, Any]) -> str | None:
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


def parse_retry_after_seconds(response: httpx.Response) -> float | None:
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


def extract_http_error_details(response: httpx.Response) -> dict[str, str | None]:
    payload_excerpt: str | None = None
    provider_error_code: str | None = None
    provider_message: str | None = None

    raw_text = response.text.strip()
    if raw_text:
        payload_excerpt = sanitize_sensitive_text(raw_text)
        if payload_excerpt is not None:
            payload_excerpt = payload_excerpt[:400]

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
                provider_message = sanitize_sensitive_text(message.strip())
        elif isinstance(error_payload, str) and error_payload.strip():
            provider_message = sanitize_sensitive_text(error_payload.strip())
        elif payload_excerpt is None:
            payload_excerpt = sanitize_sensitive_text(str(payload))
            if payload_excerpt is not None:
                payload_excerpt = payload_excerpt[:400]

    return {
        "provider_error_code": provider_error_code,
        "provider_message": provider_message,
        "payload_excerpt": payload_excerpt,
    }


async def post_json_with_vision_errors(
    *,
    client: httpx.AsyncClient,
    url: str,
    request_body: Mapping[str, Any],
    query_params: Mapping[str, Any] | None = None,
) -> httpx.Response:
    try:
        response = await client.post(url, json=request_body, params=query_params)
    except httpx.ReadTimeout as exc:
        raise VisionProviderError(
            provider_error_code="provider_read_timeout",
            provider_message="Vision provider request timed out while waiting for a response",
        ) from exc
    except httpx.RequestError as exc:
        request_url: str | None = None
        if exc.request is not None:
            request_url = sanitize_url_for_logging(str(exc.request.url))
        transport_message = sanitize_sensitive_text(str(exc))
        if request_url:
            provider_message = f"{type(exc).__name__} while requesting {request_url}"
        else:
            provider_message = f"{type(exc).__name__}: {transport_message or 'request failed'}"
        raise VisionProviderError(
            provider_error_code="provider_transport_error",
            provider_message=provider_message,
        ) from exc

    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        error_details = extract_http_error_details(exc.response)
        if exc.response.status_code == 429:
            raise VisionRateLimitError(
                retry_after_seconds=parse_retry_after_seconds(exc.response),
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


def is_response_format_compatibility_error(error: VisionProviderError) -> bool:
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
    return any(marker in message for marker in structured_output_markers)


def is_max_completion_tokens_compatibility_error(error: VisionProviderError) -> bool:
    if error.status_code != 400:
        return False
    message = " ".join(
        [
            (error.provider_message or "").strip().lower(),
            (error.payload_excerpt or "").strip().lower(),
        ]
    ).strip()
    if "max_completion_tokens" not in message and "max completion tokens" not in message:
        return False
    code = (error.provider_error_code or "").strip().lower()
    if not code:
        return True
    return (
        "unknown_parameter" in code
        or "invalid_parameter" in code
        or "unsupported" in code
    )


def provider_error_from_exception(
    *,
    message: str,
    error_code: str,
    payload_excerpt: str | None = None,
) -> VisionProviderError:
    return VisionProviderError(
        provider_error_code=error_code,
        provider_message=sanitize_sensitive_text(message),
        payload_excerpt=sanitize_sensitive_text(payload_excerpt),
    )


def safe_json_excerpt(payload: object) -> str | None:
    try:
        serialized = json.dumps(payload)
    except Exception:
        serialized = str(payload)
    sanitized = sanitize_sensitive_text(serialized)
    if sanitized is None:
        return None
    return sanitized[:400]
