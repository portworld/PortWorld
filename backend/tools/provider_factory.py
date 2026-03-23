from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from backend.core.settings import Settings
from backend.tools.providers.tavily import TavilySearchProvider
from backend.tools.search import SearchProvider


class SearchProviderBuilder(Protocol):
    def __call__(self, settings: Settings) -> SearchProvider: ...


class SearchProviderEnabledCheck(Protocol):
    def __call__(self, settings: Settings) -> bool: ...


@dataclass(frozen=True, slots=True)
class SearchProviderDefinition:
    name: str
    build_provider: SearchProviderBuilder
    is_enabled: SearchProviderEnabledCheck


class SearchProviderRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, SearchProviderDefinition] = {}

    def register(self, definition: SearchProviderDefinition) -> None:
        if definition.name in self._definitions:
            raise ValueError(f"Search provider already registered: {definition.name}")
        self._definitions[definition.name] = definition

    def resolve(self, name: str) -> SearchProviderDefinition:
        try:
            return self._definitions[name]
        except KeyError as exc:
            supported = ", ".join(sorted(self._definitions))
            raise RuntimeError(
                f"Unsupported REALTIME_WEB_SEARCH_PROVIDER='{name}'. Supported values: {supported}"
            ) from exc


def build_default_search_provider_registry() -> SearchProviderRegistry:
    registry = SearchProviderRegistry()
    registry.register(
        SearchProviderDefinition(
            name="tavily",
            build_provider=lambda settings: TavilySearchProvider(
                api_key=settings.tavily_api_key.strip(),
                timeout_ms=settings.realtime_tool_timeout_ms,
                base_url=settings.tavily_base_url,
            ),
            is_enabled=lambda settings: settings.has_tavily_api_key(),
        )
    )
    return registry


@dataclass(frozen=True, slots=True)
class SearchProviderFactory:
    settings: Settings
    registry: SearchProviderRegistry = field(default_factory=build_default_search_provider_registry)
    _definition: SearchProviderDefinition = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_definition",
            self.registry.resolve(self.settings.realtime_web_search_provider),
        )

    @property
    def provider_name(self) -> str:
        return self._definition.name

    def is_enabled(self) -> bool:
        return self._definition.is_enabled(self.settings)

    def build_if_enabled(self) -> tuple[str | None, SearchProvider | None]:
        if not self.is_enabled():
            return None, None
        return self.provider_name, self._definition.build_provider(self.settings)
