from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from backend.core.settings import Settings
from backend.vision.contracts import VisionAnalyzer


class VisionAnalyzerBuilder(Protocol):
    def __call__(self, *, settings: Settings) -> VisionAnalyzer: ...


class VisionSettingsValidator(Protocol):
    def __call__(self, settings: Settings) -> None: ...


@dataclass(frozen=True, slots=True)
class VisionProviderCapabilities:
    structured_output: bool
    image_transport: str
    retry_hint: str | None = None
    rate_limit_hint: str | None = None


@dataclass(frozen=True, slots=True)
class VisionProviderDefinition:
    name: str
    build_analyzer: VisionAnalyzerBuilder
    validate_settings: VisionSettingsValidator
    capabilities: VisionProviderCapabilities = field(
        default_factory=lambda: VisionProviderCapabilities(
            structured_output=False,
            image_transport="unknown",
            retry_hint=None,
            rate_limit_hint=None,
        )
    )


class VisionProviderRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, VisionProviderDefinition] = {}

    def register(self, definition: VisionProviderDefinition) -> None:
        if definition.name in self._definitions:
            raise ValueError(f"Vision provider already registered: {definition.name}")
        self._definitions[definition.name] = definition

    def resolve(self, name: str) -> VisionProviderDefinition:
        try:
            return self._definitions[name]
        except KeyError as exc:
            supported = ", ".join(sorted(self._definitions))
            raise ValueError(
                f"Unsupported VISION_MEMORY_PROVIDER={name!r}. Supported values: {supported}"
            ) from exc


def build_default_vision_provider_registry() -> VisionProviderRegistry:
    from backend.vision.providers.azure_openai import (
        build_azure_openai_vision_analyzer,
        validate_azure_openai_vision_settings,
    )
    from backend.vision.providers.bedrock import (
        build_bedrock_vision_analyzer,
        validate_bedrock_vision_settings,
    )
    from backend.vision.providers.claude import (
        build_claude_vision_analyzer,
        validate_claude_vision_settings,
    )
    from backend.vision.providers.gemini import (
        build_gemini_vision_analyzer,
        validate_gemini_vision_settings,
    )
    from backend.vision.providers.groq import (
        build_groq_vision_analyzer,
        validate_groq_vision_settings,
    )
    from backend.vision.providers.mistral import (
        build_mistral_vision_analyzer,
        validate_mistral_vision_settings,
    )
    from backend.vision.providers.openai import (
        build_openai_vision_analyzer,
        validate_openai_vision_settings,
    )

    registry = VisionProviderRegistry()
    registry.register(
        VisionProviderDefinition(
            name="mistral",
            build_analyzer=build_mistral_vision_analyzer,
            validate_settings=validate_mistral_vision_settings,
            capabilities=VisionProviderCapabilities(
                structured_output=False,
                image_transport="data_url",
                retry_hint="provider_managed",
                rate_limit_hint="provider_managed",
            ),
        )
    )
    registry.register(
        VisionProviderDefinition(
            name="openai",
            build_analyzer=build_openai_vision_analyzer,
            validate_settings=validate_openai_vision_settings,
            capabilities=VisionProviderCapabilities(
                structured_output=True,
                image_transport="data_url",
                retry_hint="provider_managed",
                rate_limit_hint="provider_managed",
            ),
        )
    )
    registry.register(
        VisionProviderDefinition(
            name="azure_openai",
            build_analyzer=build_azure_openai_vision_analyzer,
            validate_settings=validate_azure_openai_vision_settings,
            capabilities=VisionProviderCapabilities(
                structured_output=True,
                image_transport="data_url",
                retry_hint="provider_managed",
                rate_limit_hint="provider_managed",
            ),
        )
    )
    registry.register(
        VisionProviderDefinition(
            name="gemini",
            build_analyzer=build_gemini_vision_analyzer,
            validate_settings=validate_gemini_vision_settings,
            capabilities=VisionProviderCapabilities(
                structured_output=True,
                image_transport="inline_base64",
                retry_hint="provider_managed",
                rate_limit_hint="provider_managed",
            ),
        )
    )
    registry.register(
        VisionProviderDefinition(
            name="claude",
            build_analyzer=build_claude_vision_analyzer,
            validate_settings=validate_claude_vision_settings,
            capabilities=VisionProviderCapabilities(
                structured_output=False,
                image_transport="inline_base64",
                retry_hint="provider_managed",
                rate_limit_hint="provider_managed",
            ),
        )
    )
    registry.register(
        VisionProviderDefinition(
            name="bedrock",
            build_analyzer=build_bedrock_vision_analyzer,
            validate_settings=validate_bedrock_vision_settings,
            capabilities=VisionProviderCapabilities(
                structured_output=False,
                image_transport="native_bytes",
                retry_hint="aws_sdk_managed",
                rate_limit_hint="provider_managed",
            ),
        )
    )
    registry.register(
        VisionProviderDefinition(
            name="groq",
            build_analyzer=build_groq_vision_analyzer,
            validate_settings=validate_groq_vision_settings,
            capabilities=VisionProviderCapabilities(
                structured_output=True,
                image_transport="data_url",
                retry_hint="provider_managed",
                rate_limit_hint="provider_managed",
            ),
        )
    )
    return registry


@dataclass(frozen=True, slots=True)
class VisionAnalyzerFactory:
    settings: Settings
    registry: VisionProviderRegistry = field(
        default_factory=build_default_vision_provider_registry
    )
    _definition: VisionProviderDefinition = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_definition",
            self.registry.resolve(self.settings.vision_memory_provider),
        )

    @property
    def provider_name(self) -> str:
        return self._definition.name

    @property
    def provider_capabilities(self) -> VisionProviderCapabilities:
        return self._definition.capabilities

    @property
    def capabilities(self) -> VisionProviderCapabilities:
        return self.provider_capabilities

    def validate_configuration(self) -> None:
        self._definition.validate_settings(self.settings)

    def build_analyzer(self) -> VisionAnalyzer:
        return self._definition.build_analyzer(settings=self.settings)


def build_vision_analyzer(*, settings: Settings) -> VisionAnalyzer:
    return VisionAnalyzerFactory(settings=settings).build_analyzer()
