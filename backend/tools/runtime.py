from __future__ import annotations

import asyncio
from json import JSONDecodeError
from dataclasses import dataclass

from backend.core.settings import Settings
from backend.core.storage import BackendStorage
from backend.tools.contracts import ToolCall, ToolDefinition, ToolResult
from backend.tools.memory import MemoryToolExecutor
from backend.tools.providers.tavily import TavilySearchProvider
from backend.tools.registry import RealtimeToolRegistry, ToolRegistryError, UnknownToolError
from backend.tools.search import SearchProvider
from backend.tools.web_search import WebSearchToolExecutor


SUPPORTED_WEB_SEARCH_PROVIDERS = {"tavily"}
SUPPORTED_PROFILE_FIELDS = ("name", "job", "company", "preferences", "projects")


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

    async def execute(self, call: ToolCall) -> ToolResult:
        try:
            return await asyncio.wait_for(
                self.registry.execute(call),
                timeout=max(0.1, self.tool_timeout_ms / 1000.0),
            )
        except asyncio.TimeoutError:
            return ToolResult(
                ok=False,
                name=call.name,
                call_id=call.call_id,
                payload={"session_id": call.session_id},
                error_code="TOOL_TIMEOUT",
                error_message=f"Tool execution timed out after {self.tool_timeout_ms}ms",
            )
        except UnknownToolError as exc:
            return ToolResult(
                ok=False,
                name=call.name,
                call_id=call.call_id,
                payload={"session_id": call.session_id},
                error_code="UNKNOWN_TOOL",
                error_message=str(exc),
            )
        except ToolRegistryError as exc:
            return ToolResult(
                ok=False,
                name=call.name,
                call_id=call.call_id,
                payload={"session_id": call.session_id},
                error_code="TOOL_EXECUTION_FAILED",
                error_message=str(exc),
            )
        except Exception as exc:  # pragma: no cover - defensive fallback
            return ToolResult(
                ok=False,
                name=call.name,
                call_id=call.call_id,
                payload={"session_id": call.session_id},
                error_code="TOOL_EXECUTION_FAILED",
                error_message=str(exc),
            )

    def build_session_instructions(self, *, base_instructions: str) -> str:
        try:
            profile = self.storage.read_user_profile()
        except (JSONDecodeError, OSError):
            return base_instructions

        profile_lines = self._build_profile_lines(profile)
        if not profile_lines:
            return base_instructions
        profile_block = "\n".join(
            [
                "",
                "Stable user profile context:",
                *profile_lines,
            ]
        )
        return base_instructions.rstrip() + "\n" + profile_block + "\n"

    @staticmethod
    def _build_profile_lines(profile: dict[str, object]) -> list[str]:
        lines: list[str] = []
        for field_name in SUPPORTED_PROFILE_FIELDS:
            value = profile.get(field_name)
            rendered = RealtimeToolingRuntime._render_profile_value(value)
            if not rendered:
                continue
            label = field_name.replace("_", " ").title()
            lines.append(f"- {label}: {rendered}")
        return lines

    @staticmethod
    def _render_profile_value(value: object) -> str:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized
        if isinstance(value, list):
            rendered_items = [
                item.strip()
                for item in value
                if isinstance(item, str) and item.strip()
            ]
            return ", ".join(rendered_items)
        return ""

    async def startup(self) -> None:
        if self.search_provider is not None:
            await self.search_provider.startup()

    async def shutdown(self) -> None:
        if self.search_provider is not None:
            await self.search_provider.shutdown()
