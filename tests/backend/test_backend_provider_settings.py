from __future__ import annotations

import os
import unittest

from backend.core.settings import Settings
from backend.realtime.contracts import RealtimeProviderCapabilities
from backend.realtime.factory import (
    RealtimeProviderFactory,
    build_default_realtime_provider_registry,
)
from backend.realtime.providers.openai import OPENAI_REALTIME_CAPABILITIES
from backend.vision.providers.mistral.analyzer import build_mistral_vision_analyzer


class BackendProviderSettingsTests(unittest.TestCase):
    def _settings(self, extra_env: dict[str, str]) -> Settings:
        with unittest.mock.patch.dict(os.environ, extra_env, clear=True):
            return Settings.from_env()

    def test_gemini_live_realtime_settings_are_loaded(self) -> None:
        settings = self._settings(
            {
                "REALTIME_PROVIDER": "gemini_live",
                "GEMINI_LIVE_API_KEY": "gemini-live-key",
                "GEMINI_LIVE_MODEL": "gemini-live-model",
                "GEMINI_LIVE_BASE_URL": "https://example.test",
                "GEMINI_LIVE_ENDPOINT": "/live/ws",
            }
        )
        self.assertEqual(settings.require_realtime_api_key(provider="gemini_live"), "gemini-live-key")
        self.assertEqual(settings.resolve_realtime_model(provider="gemini_live"), "gemini-live-model")
        self.assertEqual(settings.resolve_realtime_base_url(provider="gemini_live"), "https://example.test")
        self.assertEqual(settings.resolve_realtime_endpoint(provider="gemini_live"), "/live/ws")

    def test_azure_openai_vision_settings_are_loaded(self) -> None:
        settings = self._settings(
            {
                "VISION_MEMORY_ENABLED": "true",
                "VISION_MEMORY_PROVIDER": "azure_openai",
                "VISION_AZURE_OPENAI_MODEL": "ignored-fallback",
                "VISION_AZURE_OPENAI_API_KEY": "azure-key",
                "VISION_AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com",
                "VISION_AZURE_OPENAI_API_VERSION": "2024-10-21",
                "VISION_AZURE_OPENAI_DEPLOYMENT": "vision-deployment",
            }
        )
        self.assertEqual(settings.require_vision_provider_api_key(provider="azure_openai"), "azure-key")
        self.assertEqual(
            settings.resolve_vision_provider_endpoint(provider="azure_openai"),
            "https://example.openai.azure.com",
        )
        self.assertEqual(
            settings.resolve_vision_provider_api_version(provider="azure_openai"),
            "2024-10-21",
        )
        self.assertEqual(
            settings.resolve_vision_provider_deployment(provider="azure_openai"),
            "vision-deployment",
        )

    def test_mistral_vision_settings_are_loaded(self) -> None:
        settings = self._settings(
            {
                "VISION_MEMORY_ENABLED": "true",
                "VISION_MEMORY_PROVIDER": "mistral",
                "VISION_MISTRAL_API_KEY": "mistral-key",
                "VISION_MISTRAL_BASE_URL": "https://mistral.example.test",
            }
        )
        self.assertEqual(settings.require_vision_provider_api_key(provider="mistral"), "mistral-key")
        self.assertEqual(
            settings.resolve_vision_provider_base_url(provider="mistral"),
            "https://mistral.example.test",
        )

    def test_mistral_vision_analyzer_builds_from_provider_scoped_settings(self) -> None:
        settings = self._settings(
            {
                "VISION_MEMORY_ENABLED": "true",
                "VISION_MEMORY_PROVIDER": "mistral",
                "VISION_MISTRAL_MODEL": "pixtral-large-latest",
                "VISION_MISTRAL_API_KEY": "mistral-key",
                "VISION_MISTRAL_BASE_URL": "https://mistral.example.test",
            }
        )
        analyzer = build_mistral_vision_analyzer(settings=settings)
        self.assertEqual(analyzer.api_key, "mistral-key")
        self.assertEqual(analyzer.model_name, "pixtral-large-latest")
        self.assertEqual(analyzer.base_url, "https://mistral.example.test")

    def test_provider_scoped_vision_models_are_resolved_independently(self) -> None:
        settings = self._settings(
            {
                "VISION_MEMORY_ENABLED": "true",
                "VISION_MEMORY_PROVIDER": "openai",
                "VISION_OPENAI_MODEL": "gpt-4.1-mini",
                "VISION_GEMINI_MODEL": "gemini-2.0-flash",
            }
        )

        self.assertEqual(
            settings.resolve_vision_provider_model(provider="openai"),
            "gpt-4.1-mini",
        )
        self.assertEqual(
            settings.resolve_vision_provider_model(provider="gemini"),
            "gemini-2.0-flash",
        )

    def test_vision_provider_model_defaults_are_provider_scoped(self) -> None:
        settings = self._settings(
            {
                "VISION_MEMORY_ENABLED": "true",
                "VISION_MEMORY_PROVIDER": "openai",
            }
        )

        self.assertEqual(settings.resolve_vision_provider_model(provider="mistral"), "ministral-3b-2512")
        self.assertEqual(settings.resolve_vision_provider_model(provider="openai"), "gpt-5.4-nano")
        self.assertEqual(
            settings.resolve_vision_provider_model(provider="gemini"),
            "gemini-3.1-flash-lite-preview",
        )
        self.assertEqual(settings.resolve_vision_provider_model(provider="claude"), "claude-haiku-4-5")
        self.assertEqual(
            settings.resolve_vision_provider_model(provider="bedrock"),
            "mistral.ministral-3-3b-instruct",
        )
        self.assertEqual(
            settings.resolve_vision_provider_model(provider="groq"),
            "meta-llama/llama-4-scout-17b-16e-instruct",
        )

    def test_openai_realtime_registry_exports_capabilities(self) -> None:
        registry = build_default_realtime_provider_registry()
        definition = registry.resolve("openai")

        self.assertIs(definition.capabilities, OPENAI_REALTIME_CAPABILITIES)
        self.assertIsInstance(definition.capabilities, RealtimeProviderCapabilities)
        self.assertTrue(definition.capabilities.streaming_audio_input)
        self.assertEqual(definition.capabilities.tool_result_submission_mode, "conversation_item")

    def test_openai_realtime_factory_initializes_with_registry(self) -> None:
        settings = self._settings(
            {
                "OPENAI_API_KEY": "test-key",
            }
        )

        factory = RealtimeProviderFactory(settings=settings)

        self.assertEqual(factory.provider_name, "openai")
        self.assertIs(factory.capabilities, OPENAI_REALTIME_CAPABILITIES)


if __name__ == "__main__":
    unittest.main()
