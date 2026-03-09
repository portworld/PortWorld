from __future__ import annotations

from dataclasses import dataclass

from backend.core.settings import Settings
from backend.core.storage import BackendStorage
from backend.tools.contracts import ToolDefinition
from backend.tools.registry import NotImplementedToolExecutor, RealtimeToolRegistry


SUPPORTED_WEB_SEARCH_PROVIDERS = {"tavily"}


@dataclass(frozen=True, slots=True)
class RealtimeToolingRuntime:
    settings: Settings
    storage: BackendStorage
    web_search_enabled: bool
    web_search_provider: str | None
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
        registry = cls._build_registry(web_search_enabled=web_search_enabled)
        return cls(
            settings=settings,
            storage=storage,
            web_search_enabled=web_search_enabled,
            web_search_provider=web_search_provider,
            tool_timeout_ms=settings.realtime_tool_timeout_ms,
            web_search_max_results=settings.realtime_web_search_max_results,
            registry=registry,
        )

    @staticmethod
    def _build_registry(*, web_search_enabled: bool) -> RealtimeToolRegistry:
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
            executor=NotImplementedToolExecutor(
                tool_name="get_short_term_visual_context"
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
            executor=NotImplementedToolExecutor(tool_name="get_session_visual_context"),
        )
        if web_search_enabled:
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
                executor=NotImplementedToolExecutor(tool_name="web_search"),
            )
        return registry

    def list_tool_definitions(self) -> list[ToolDefinition]:
        return self.registry.list_definitions()

    def to_openai_tools(self) -> list[dict[str, object]]:
        return self.registry.to_openai_tools()

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None
