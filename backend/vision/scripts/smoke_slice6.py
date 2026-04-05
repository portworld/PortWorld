from __future__ import annotations

import asyncio
import os
import time
from contextlib import contextmanager

from pydantic import ValidationError

from backend.core.settings import Settings
from backend.vision.contracts import VisionFrameContext, VisionProviderError
from backend.vision.factory import VisionAnalyzerFactory, build_default_vision_provider_registry
from backend.vision.providers.azure_openai.analyzer import AzureOpenAIVisionAnalyzer
from backend.vision.providers.bedrock.analyzer import BedrockVisionAnalyzer
from backend.vision.providers.claude.analyzer import ClaudeVisionAnalyzer
from backend.vision.providers.gemini.analyzer import GeminiVisionAnalyzer
from backend.vision.providers.groq.analyzer import GroqVisionAnalyzer
from backend.vision.providers.mistral.analyzer import MistralVisionAnalyzer
from backend.vision.providers.nvidia_integrate.analyzer import (
    NVIDIA_FALLBACK_SYSTEM_PROMPT,
    NvidiaIntegrateVisionAnalyzer,
    _normalize_nvidia_fallback_payload,
)
from backend.vision.providers.openai.analyzer import OpenAIVisionAnalyzer
from backend.vision.providers.shared import normalize_observation
from backend.ws.session.session_registry import SessionRecord
from backend.ws.session.session_runtime import _close_finalize_and_mark_ended


@contextmanager
def temporary_env(overrides: dict[str, str | None]):
    previous = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(previous)
        for key, value in overrides.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


def _assert_validation_passes(provider: str, env: dict[str, str | None]) -> None:
    with temporary_env(env | {"VISION_MEMORY_PROVIDER": provider, "VISION_MEMORY_ENABLED": "true"}):
        settings = Settings.from_env()
        factory = VisionAnalyzerFactory(settings=settings)
        factory.validate_configuration()


def _assert_validation_fails(provider: str, env: dict[str, str | None]) -> None:
    with temporary_env(env | {"VISION_MEMORY_PROVIDER": provider, "VISION_MEMORY_ENABLED": "true"}):
        settings = Settings.from_env()
        factory = VisionAnalyzerFactory(settings=settings)
        try:
            factory.validate_configuration()
        except RuntimeError:
            return
    raise AssertionError(f"Expected validation to fail for provider={provider}")


def _assert_payload_parsing() -> None:
    frame_context = VisionFrameContext(
        frame_id="frame-1",
        session_id="session-1",
        capture_ts_ms=1000,
        width=1280,
        height=720,
    )

    mistral_payload = MistralVisionAnalyzer(api_key="k", model_name="m")._extract_provider_payload(
        {
            "choices": [
                {
                    "message": {
                        "content": '{"scene_summary":"desk","user_activity_guess":"typing","entities":["laptop"],"actions":["typing"],"visible_text":[],"documents_seen":[],"salient_change":false,"confidence":0.8}'
                    }
                }
            ]
        }
    )
    normalize_observation(payload=mistral_payload, frame_context=frame_context)

    openai_payload = OpenAIVisionAnalyzer(api_key="k", model_name="m")._extract_provider_payload(
        {
            "choices": [
                {
                    "message": {
                        "content": '{"scene_summary":"street","user_activity_guess":"walking","entities":["car"],"actions":["walking"],"visible_text":[],"documents_seen":[],"salient_change":true,"confidence":0.7}'
                    }
                }
            ]
        }
    )
    normalize_observation(payload=openai_payload, frame_context=frame_context)

    azure_payload = AzureOpenAIVisionAnalyzer(
        api_key="k",
        deployment="dep",
        endpoint="https://example.openai.azure.com",
    )._extract_provider_payload(
        {
            "choices": [
                {
                    "message": {
                        "content": '{"scene_summary":"office","user_activity_guess":"reading","entities":["paper"],"actions":["reading"],"visible_text":["Agenda"],"documents_seen":["Agenda"],"salient_change":false,"confidence":0.6}'
                    }
                }
            ]
        }
    )
    normalize_observation(payload=azure_payload, frame_context=frame_context)

    gemini_payload = GeminiVisionAnalyzer(api_key="k", model_name="gemini-2.0-flash")._extract_provider_payload(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": '{"scene_summary":"kitchen","user_activity_guess":"cooking","entities":["pan"],"actions":["stirring"],"visible_text":[],"documents_seen":[],"salient_change":true,"confidence":0.75}'
                            }
                        ]
                    }
                }
            ]
        }
    )
    normalize_observation(payload=gemini_payload, frame_context=frame_context)

    claude_payload = ClaudeVisionAnalyzer(api_key="k", model_name="claude-3-5-sonnet")._extract_provider_payload(
        {
            "content": [
                {
                    "type": "text",
                    "text": '{"scene_summary":"store","user_activity_guess":"shopping","entities":["shelf"],"actions":["looking"],"visible_text":["SALE"],"documents_seen":[],"salient_change":false,"confidence":0.65}',
                }
            ]
        }
    )
    normalize_observation(payload=claude_payload, frame_context=frame_context)

    bedrock_payload = BedrockVisionAnalyzer(model_name="anthropic.claude", region_name="us-east-1")._extract_provider_payload(
        {
            "output": {
                "message": {
                    "content": [
                        {
                            "text": '{"scene_summary":"park","user_activity_guess":"sitting","entities":["bench"],"actions":["resting"],"visible_text":[],"documents_seen":[],"salient_change":false,"confidence":0.55}'
                        }
                    ]
                }
            }
        }
    )
    normalize_observation(payload=bedrock_payload, frame_context=frame_context)

    groq_payload = GroqVisionAnalyzer(api_key="k", model_name="llama-3.2-90b-vision-preview")._extract_provider_payload(
        {
            "choices": [
                {
                    "message": {
                        "content": '{"scene_summary":"train","user_activity_guess":"commuting","entities":["window"],"actions":["sitting"],"visible_text":[],"documents_seen":[],"salient_change":true,"confidence":0.7}'
                    }
                }
            ]
        }
    )
    normalize_observation(payload=groq_payload, frame_context=frame_context)


def _assert_request_shapes() -> None:
    frame_context = VisionFrameContext(
        frame_id="frame-1",
        session_id="session-1",
        capture_ts_ms=1000,
        width=1280,
        height=720,
    )
    image_bytes = b"jpeg-bytes"

    mistral_request = MistralVisionAnalyzer(api_key="k", model_name="ministral-3b-2512")._build_request_body(
        image_bytes=image_bytes,
        frame_context=frame_context,
        image_media_type="image/jpeg",
        include_response_format=True,
    )
    mistral_content = mistral_request["messages"][1]["content"]
    assert mistral_content[1]["type"] == "image_url"
    assert isinstance(mistral_content[1]["image_url"], str)
    assert mistral_content[1]["image_url"].startswith("data:image/jpeg;base64,")

    nvidia_request = NvidiaIntegrateVisionAnalyzer(
        api_key="k",
        model_name="mistralai/ministral-14b-instruct-2512",
    )
    assert nvidia_request._supports_response_format is False

    request_body = nvidia_request._build_request_body(
        image_bytes=image_bytes,
        frame_context=frame_context,
        image_media_type="image/jpeg",
        include_response_format=nvidia_request._supports_response_format,
        use_legacy_max_tokens=False,
    )
    nvidia_content = request_body["messages"][1]["content"]
    assert nvidia_content[1]["type"] == "image_url"
    assert nvidia_content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "response_format" not in request_body

    nvidia_fallback_request = NvidiaIntegrateVisionAnalyzer(
        api_key="k",
        model_name="mistralai/ministral-14b-instruct-2512",
    )._build_request_body(
        image_bytes=image_bytes,
        frame_context=frame_context,
        image_media_type="image/jpeg",
        include_response_format=False,
        use_legacy_max_tokens=False,
    )
    assert nvidia_fallback_request["messages"][0]["content"] == NVIDIA_FALLBACK_SYSTEM_PROMPT


def _assert_nvidia_truncated_payload_classification() -> None:
    try:
        NvidiaIntegrateVisionAnalyzer(
            api_key="k",
            model_name="mistralai/ministral-14b-instruct-2512",
        )._extract_provider_payload(
            {
                "choices": [
                    {
                        "message": {
                            "content": """```json
{
  "scene_summary": "User seated at a table with an open laptop and external keyboard, surrounded by personal items.",
  "user_activity_guess": "working",
  "entities": ["laptop", "external keyboard", "mouse", "notebook"
```"""
                        }
                    }
                ]
            }
        )
    except VisionProviderError as exc:
        assert exc.provider_error_code == "provider_payload_truncated_json"
    else:
        raise AssertionError("Expected truncated NVIDIA payload to be classified explicitly")


async def _assert_session_teardown_does_not_wait_for_consolidation() -> None:
    class DummyBridge:
        async def close(self) -> None:
            return None

    class DummyStorage:
        def __init__(self) -> None:
            self.status_updates: list[tuple[str, str]] = []

        def upsert_session_status(self, *, session_id: str, status: str) -> None:
            self.status_updates.append((session_id, status))

    class SlowDurableMemoryRuntime:
        def __init__(self) -> None:
            self.completed = asyncio.Event()

        async def finalize_session(self, *, session_id: str) -> str:
            await asyncio.sleep(0.2)
            self.completed.set()
            return "completed"

    session = SessionRecord(
        session_id="session-1",
        websocket=object(),  # type: ignore[arg-type]
        bridge=DummyBridge(),
    )
    storage = DummyStorage()
    durable_memory_runtime = SlowDurableMemoryRuntime()

    start = time.perf_counter()
    await _close_finalize_and_mark_ended(
        active_session=session,
        storage=storage,
        vision_memory_runtime=None,
        durable_memory_runtime=durable_memory_runtime,  # type: ignore[arg-type]
    )
    elapsed = time.perf_counter() - start

    assert elapsed < 0.1, f"Session close took too long: {elapsed:.3f}s"
    assert storage.status_updates == [("session-1", "ended")]
    await asyncio.wait_for(durable_memory_runtime.completed.wait(), timeout=1.0)


def _assert_nvidia_fallback_normalization() -> None:
    normalized = _normalize_nvidia_fallback_payload(
        """```json
{
  "scene_summary": "MacBook open on a desk.",
  "user_activity_guess": ["coding", "network_troubleshooting"],
  "entities": "MacBook",
  "actions": ["typing"],
  "visible_text": "ifconfig",
  "documents_seen": null,
  "salient_change": "false",
  "confidence": "82%"
}
```"""
    )
    assert normalized["user_activity_guess"] == "coding, network_troubleshooting"
    assert normalized["entities"] == ["MacBook"]
    assert normalized["visible_text"] == ["ifconfig"]
    assert "documents_seen" not in normalized or normalized["documents_seen"] == []

    payload = NvidiaIntegrateVisionAnalyzer(
        api_key="k",
        model_name="mistralai/ministral-14b-instruct-2512",
    )._extract_provider_payload(
        {
            "choices": [
                {
                    "message": {
                        "content": """```json
{
  "scene_summary": "MacBook open on a desk.",
  "user_activity_guess": ["coding", "network_troubleshooting"],
  "entities": "MacBook",
  "actions": ["typing"],
  "visible_text": "ifconfig",
  "documents_seen": null,
  "salient_change": "false",
  "confidence": "82%"
}
```"""
                    }
                }
            ]
        }
    )
    assert payload.user_activity_guess == "coding, network_troubleshooting"
    assert payload.entities == ["MacBook"]
    assert payload.visible_text == ["ifconfig"]
    assert payload.salient_change is False
    assert payload.confidence == 0.82

    try:
        NvidiaIntegrateVisionAnalyzer(
            api_key="k",
            model_name="mistralai/ministral-14b-instruct-2512",
        )._extract_provider_payload(
            {
                "choices": [
                    {
                        "message": {
                            "content": """```json
{
  "user_activity_guess": ["coding"]
}
```"""
                        }
                    }
                ]
            }
        )
    except VisionProviderError:
        pass
    except ValidationError:
        pass
    except ValueError:
        pass
    else:
        raise AssertionError("Expected missing required NVIDIA fallback fields to fail")


def main() -> None:
    registry = build_default_vision_provider_registry()
    for provider in [
        "mistral",
        "nvidia_integrate",
        "openai",
        "azure_openai",
        "gemini",
        "claude",
        "bedrock",
        "groq",
    ]:
        registry.resolve(provider)

    _assert_validation_passes(
        "mistral",
        {
            "VISION_MISTRAL_API_KEY": "mistral-key",
            "VISION_MISTRAL_MODEL": "ministral-3b-2512",
        },
    )
    _assert_validation_fails(
        "mistral",
        {
            "VISION_MISTRAL_API_KEY": None,
            "VISION_PROVIDER_API_KEY": None,
            "MISTRAL_API_KEY": None,
        },
    )
    _assert_validation_fails(
        "mistral",
        {
            "VISION_MISTRAL_API_KEY": "mistral-key",
            "VISION_MISTRAL_MODEL": "mistralai/ministral-14b-instruct-2512",
        },
    )
    _assert_validation_fails(
        "mistral",
        {
            "VISION_MISTRAL_API_KEY": "mistral-key",
            "VISION_MISTRAL_BASE_URL": "https://integrate.api.nvidia.com",
        },
    )
    _assert_validation_passes(
        "nvidia_integrate",
        {
            "VISION_NVIDIA_API_KEY": "nvidia-key",
            "VISION_NVIDIA_MODEL": "mistralai/ministral-14b-instruct-2512",
        },
    )
    _assert_validation_fails(
        "nvidia_integrate",
        {
            "VISION_NVIDIA_API_KEY": None,
            "VISION_NVIDIA_MODEL": "mistralai/ministral-14b-instruct-2512",
        },
    )

    _assert_validation_passes(
        "openai",
        {
            "VISION_OPENAI_API_KEY": "openai-key",
            "VISION_OPENAI_MODEL": "gpt-4.1-mini",
        },
    )
    _assert_validation_fails(
        "openai",
        {
            "VISION_OPENAI_API_KEY": None,
            "VISION_PROVIDER_API_KEY": None,
            "VISION_OPENAI_MODEL": "gpt-4.1-mini",
        },
    )

    _assert_validation_passes(
        "azure_openai",
        {
            "VISION_AZURE_OPENAI_API_KEY": "azure-key",
            "VISION_AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com",
            "VISION_AZURE_OPENAI_DEPLOYMENT": "gpt-4.1-mini",
            "VISION_AZURE_OPENAI_API_VERSION": "2024-10-21",
        },
    )
    _assert_validation_fails(
        "azure_openai",
        {
            "VISION_AZURE_OPENAI_API_KEY": "azure-key",
            "VISION_AZURE_OPENAI_ENDPOINT": None,
            "VISION_AZURE_OPENAI_DEPLOYMENT": "gpt-4.1-mini",
        },
    )

    _assert_validation_passes(
        "gemini",
        {
            "VISION_GEMINI_API_KEY": "gemini-key",
            "VISION_GEMINI_MODEL": "gemini-2.0-flash",
        },
    )
    _assert_validation_fails(
        "gemini",
        {
            "VISION_GEMINI_API_KEY": None,
            "VISION_PROVIDER_API_KEY": None,
            "VISION_GEMINI_MODEL": "gemini-2.0-flash",
        },
    )

    _assert_validation_passes(
        "claude",
        {
            "VISION_CLAUDE_API_KEY": "claude-key",
            "VISION_CLAUDE_MODEL": "claude-3-5-sonnet-latest",
        },
    )
    _assert_validation_fails(
        "claude",
        {
            "VISION_CLAUDE_API_KEY": None,
            "VISION_PROVIDER_API_KEY": None,
            "VISION_CLAUDE_MODEL": "claude-3-5-sonnet-latest",
        },
    )

    _assert_validation_passes(
        "bedrock",
        {
            "VISION_BEDROCK_REGION": "us-east-1",
            "VISION_BEDROCK_MODEL": "anthropic.claude-3-5-sonnet-20240620-v1:0",
        },
    )
    _assert_validation_fails(
        "bedrock",
        {
            "VISION_BEDROCK_REGION": None,
            "AWS_REGION": None,
            "VISION_BEDROCK_MODEL": "anthropic.claude-3-5-sonnet-20240620-v1:0",
        },
    )

    _assert_validation_passes(
        "groq",
        {
            "VISION_GROQ_API_KEY": "groq-key",
            "VISION_GROQ_MODEL": "llama-3.2-90b-vision-preview",
        },
    )
    _assert_validation_fails(
        "groq",
        {
            "VISION_GROQ_API_KEY": None,
            "VISION_PROVIDER_API_KEY": None,
            "VISION_GROQ_MODEL": "llama-3.2-90b-vision-preview",
        },
    )

    _assert_payload_parsing()
    _assert_request_shapes()
    _assert_nvidia_truncated_payload_classification()
    _assert_nvidia_fallback_normalization()
    asyncio.run(_assert_session_teardown_does_not_wait_for_consolidation())
    print("slice6 smoke checks passed")


if __name__ == "__main__":
    main()
