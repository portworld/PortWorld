from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from json import JSONDecodeError

from backend.core.settings import Settings
from backend.core.storage import BackendStorage, RealtimeReadOnlyStorageView
from backend.tools.catalog import TOOL_WEB_SEARCH, ToolCatalogContext, build_tool_registry
from backend.tools.contracts import ToolCall, ToolDefinition, ToolResult
from backend.tools.instructions import build_tool_usage_block, build_user_memory_instruction_snippet
from backend.tools.provider_factory import (
    SearchProviderBuilder,
    SearchProviderDefinition,
    SearchProviderEnabledCheck,
    SearchProviderFactory,
    SearchProviderRegistry,
    build_default_search_provider_registry,
)
from backend.tools.registry import RealtimeToolRegistry, ToolRegistryError, UnknownToolError
from backend.tools.results import tool_error
from backend.tools.search import SearchProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RealtimeToolingRuntime:
    settings: Settings
    storage: RealtimeReadOnlyStorageView
    web_search_enabled: bool
    web_search_provider: str | None
    search_provider: SearchProvider | None
    tool_timeout_ms: int
    web_search_max_results: int
    registry: RealtimeToolRegistry
    search_provider_factory: SearchProviderFactory = field(repr=False, compare=False)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        storage: BackendStorage,
    ) -> "RealtimeToolingRuntime":
        read_only_storage = storage.realtime_read_only_view()
        search_provider_factory = SearchProviderFactory(settings=settings)
        web_search_provider, search_provider = search_provider_factory.build_if_enabled()
        web_search_enabled = search_provider is not None
        registry = cls._build_registry(
            context=ToolCatalogContext(
                storage=read_only_storage,
                user_memory_storage=storage,
                search_provider=search_provider,
                web_search_provider=web_search_provider,
                web_search_max_results=settings.realtime_web_search_max_results,
            )
        )
        return cls(
            settings=settings,
            storage=read_only_storage,
            web_search_enabled=web_search_enabled,
            web_search_provider=web_search_provider,
            search_provider=search_provider,
            tool_timeout_ms=settings.realtime_tool_timeout_ms,
            web_search_max_results=settings.realtime_web_search_max_results,
            registry=registry,
            search_provider_factory=search_provider_factory,
        )

    @staticmethod
    def _build_registry(
        *,
        context: ToolCatalogContext,
    ) -> RealtimeToolRegistry:
        return build_tool_registry(context=context)

    def list_tool_definitions(self) -> list[ToolDefinition]:
        return self.registry.list_definitions()

    def to_openai_tools(self) -> list[dict[str, object]]:
        return self.registry.to_openai_tools()

    def filtered(self, *, allowed_tool_names: frozenset[str] | None) -> "RealtimeToolingRuntime":
        if allowed_tool_names is None:
            return self

        filtered_registry = self.registry.subset(allowed_tool_names)
        return RealtimeToolingRuntime(
            settings=self.settings,
            storage=self.storage,
            web_search_enabled=filtered_registry.has_tool(TOOL_WEB_SEARCH),
            web_search_provider=self.web_search_provider,
            search_provider=self.search_provider,
            tool_timeout_ms=self.tool_timeout_ms,
            web_search_max_results=self.web_search_max_results,
            registry=filtered_registry,
            search_provider_factory=self.search_provider_factory,
        )

    async def execute(self, call: ToolCall) -> ToolResult:
        try:
            return await asyncio.wait_for(
                self.registry.execute(call),
                timeout=max(0.1, self.tool_timeout_ms / 1000.0),
            )
        except asyncio.TimeoutError:
            return tool_error(
                call=call,
                error_code="TOOL_TIMEOUT",
                error_message=f"Tool execution timed out after {self.tool_timeout_ms}ms",
                payload={"session_id": call.session_id},
            )
        except UnknownToolError as exc:
            logger.warning(
                "Unknown tool requested session_id=%s call_id=%s name=%s",
                call.session_id,
                call.call_id,
                call.name,
                exc_info=exc,
            )
            return tool_error(
                call=call,
                error_code="UNKNOWN_TOOL",
                error_message="Unknown requested tool",
                payload={"session_id": call.session_id},
            )
        except ToolRegistryError as exc:
            logger.warning(
                "Tool execution failed due to registry error session_id=%s call_id=%s name=%s",
                call.session_id,
                call.call_id,
                call.name,
                exc_info=exc,
            )
            return tool_error(
                call=call,
                error_code="TOOL_EXECUTION_FAILED",
                error_message="Tool execution failed",
                payload={"session_id": call.session_id},
            )
        except Exception:  # pragma: no cover - defensive fallback
            logger.exception(
                "Unexpected tool execution failure session_id=%s call_id=%s name=%s",
                call.session_id,
                call.call_id,
                call.name,
            )
            return tool_error(
                call=call,
                error_code="TOOL_EXECUTION_FAILED",
                error_message="Tool execution failed",
                payload={"session_id": call.session_id},
            )

    def build_session_instructions(self, *, base_instructions: str) -> str:
        sections: list[str] = [base_instructions.rstrip()]

        tool_usage_block = build_tool_usage_block(registry=self.registry)
        if tool_usage_block:
            sections.append(tool_usage_block)

        try:
            user_memory_markdown = self.storage.read_user_memory()
        except JSONDecodeError:
            logger.warning("Failed to parse user memory markdown, proceeding without memory context")
            return "\n\n".join(section for section in sections if section).strip() + "\n"
        except OSError as exc:
            logger.warning(
                "Failed to read user memory from storage: %s, proceeding without memory context",
                exc,
            )
            return "\n\n".join(section for section in sections if section).strip() + "\n"

        user_memory_snippet = build_user_memory_instruction_snippet(user_memory_markdown)
        if user_memory_snippet:
            sections.append("\n".join(["Stable user memory context:", user_memory_snippet]))

        return "\n\n".join(section for section in sections if section).strip() + "\n"

    async def startup(self) -> None:
        if self.search_provider is not None:
            await self.search_provider.startup()

    async def shutdown(self) -> None:
        if self.search_provider is not None:
            await self.search_provider.shutdown()


__all__ = [
    "RealtimeToolingRuntime",
    "SearchProviderBuilder",
    "SearchProviderDefinition",
    "SearchProviderEnabledCheck",
    "SearchProviderFactory",
    "SearchProviderRegistry",
    "build_default_search_provider_registry",
]
