from __future__ import annotations

from dataclasses import dataclass

from backend.core.settings import Settings
from backend.core.storage import BackendStorage


SUPPORTED_WEB_SEARCH_PROVIDERS = {"tavily"}


@dataclass(frozen=True, slots=True)
class RealtimeToolingRuntime:
    settings: Settings
    storage: BackendStorage
    web_search_enabled: bool
    web_search_provider: str | None
    tool_timeout_ms: int
    web_search_max_results: int

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        storage: BackendStorage,
    ) -> "RealtimeToolingRuntime":
        provider = settings.realtime_web_search_provider
        if provider not in SUPPORTED_WEB_SEARCH_PROVIDERS:
            raise RuntimeError(
                "Unsupported REALTIME_WEB_SEARCH_PROVIDER="
                f"'{provider}'. Supported values: {', '.join(sorted(SUPPORTED_WEB_SEARCH_PROVIDERS))}"
            )

        web_search_enabled = settings.has_tavily_api_key()
        web_search_provider = provider if web_search_enabled else None
        return cls(
            settings=settings,
            storage=storage,
            web_search_enabled=web_search_enabled,
            web_search_provider=web_search_provider,
            tool_timeout_ms=settings.realtime_tool_timeout_ms,
            web_search_max_results=settings.realtime_web_search_max_results,
        )

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None
