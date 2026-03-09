from __future__ import annotations

from dataclasses import dataclass

from backend.core.settings import Settings
from backend.core.storage import BackendStorage
from backend.tools.contracts import ToolDefinition
from backend.tools.memory import MemoryToolExecutor
from backend.tools.providers.tavily import TavilySearchProvider
from backend.tools.registry import RealtimeToolRegistry
from backend.tools.search import SearchProvider
from backend.tools.web_search import WebSearchToolExecutor


SUPPORTED_WEB_SEARCH_PROVIDERS = {"tavily"}


@dataclass(frozen=True, slots=True)
class RealtimeToolingRuntime:
    settings: Settings
    storage: BackendStorage
    web_search_enabled: bool
    web_search_provider: str | None
    search_provider: SearchProvider | None
    tool_timeout_ms: int
    web_search_max_results: int
    registry: RealtimeToolRegistry

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
        search_provider = None
        if web_search_enabled:
            search_provider = TavilySearchProvider(
                api_key=settings.tavily_api_key.strip(),
                timeout_ms=settings.realtime_tool_timeout_ms,
                base_url=settings.tavily_base_url,
            )
        registry = cls._build_registry(
            storage=storage,
            search_provider=search_provider,
            web_search_enabled=web_search_enabled,
            web_search_provider=web_search_provider,
            web_search_max_results=settings.realtime_web_search_max_results,
        )
        return cls(
            settings=settings,
            storage=storage,
            web_search_enabled=web_search_enabled,
            web_search_provider=web_search_provider,
            search_provider=search_provider,
            tool_timeout_ms=settings.realtime_tool_timeout_ms,
            web_search_max_results=settings.realtime_web_search_max_results,
            registry=registry,
        )

    @staticmethod
    def _build_registry(
        *,
        storage: BackendStorage,
        search_provider: SearchProvider | None,
        web_search_enabled: bool,
        web_search_provider: str | None,
        web_search_max_results: int,
    ) -> RealtimeToolRegistry:
        registry = RealtimeToolRegistry()
        registry.register(
            definition=ToolDefinition(
                name="get_short_term_visual_context",
                description=(
                    "Read the current short-term visual memory for this active session."
                ),
                input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
            executor=MemoryToolExecutor(
                storage=storage,
                memory_scope="short_term",
            ),
        )
        registry.register(
            definition=ToolDefinition(
                name="get_session_visual_context",
                description=(
                    "Read the current session-level visual memory for this active session."
                ),
                input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
            executor=MemoryToolExecutor(
                storage=storage,
                memory_scope="session",
            ),
        )
        if web_search_enabled:
            if search_provider is None:
                raise RuntimeError("Search provider must exist when web search is enabled")
            registry.register(
                definition=ToolDefinition(
                    name="web_search",
                    description="Search the web for fresh external context.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "The search query to run.",
                            }
                        },
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                ),
                executor=WebSearchToolExecutor(
                    provider=search_provider,
                    provider_name=web_search_provider or "tavily",
                    max_results=web_search_max_results,
                ),
            )
        return registry

    def list_tool_definitions(self) -> list[ToolDefinition]:
        return self.registry.list_definitions()

    def to_openai_tools(self) -> list[dict[str, object]]:
        return self.registry.to_openai_tools()

    async def startup(self) -> None:
        if self.search_provider is not None:
            await self.search_provider.startup()

    async def shutdown(self) -> None:
        if self.search_provider is not None:
            await self.search_provider.shutdown()
