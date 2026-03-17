from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderEditOptions:
    realtime_provider: str | None
    with_vision: bool
    without_vision: bool
    vision_provider: str | None
    with_tooling: bool
    without_tooling: bool
    search_provider: str | None
    realtime_api_key: str | None
    vision_api_key: str | None
    search_api_key: str | None
    openai_api_key: str | None
    vision_provider_api_key: str | None
    tavily_api_key: str | None


@dataclass(frozen=True, slots=True)
class ProviderSectionResult:
    realtime_provider: str
    vision_enabled: bool
    vision_provider: str
    tooling_enabled: bool
    search_provider: str
    secret_env_updates: dict[str, str]
