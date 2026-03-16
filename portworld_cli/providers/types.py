from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderEditOptions:
    with_vision: bool
    without_vision: bool
    with_tooling: bool
    without_tooling: bool
    openai_api_key: str | None
    vision_provider_api_key: str | None
    tavily_api_key: str | None


@dataclass(frozen=True, slots=True)
class ProviderSectionResult:
    vision_enabled: bool
    tooling_enabled: bool
    openai_api_key: str
    vision_provider_api_key: str
    tavily_api_key: str
