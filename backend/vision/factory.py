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
    from backend.vision.providers.mistral import (
        build_mistral_vision_analyzer,
        validate_mistral_vision_settings,
    )

    registry = VisionProviderRegistry()
    mistral_capabilities = VisionProviderCapabilities(
        structured_output=False,
        image_transport="data_url",
        retry_hint="provider_managed",
        rate_limit_hint="provider_managed",
    )
    registry.register(
        VisionProviderDefinition(
            name="mistral",
            build_analyzer=build_mistral_vision_analyzer,
            validate_settings=validate_mistral_vision_settings,
            capabilities=mistral_capabilities,
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
