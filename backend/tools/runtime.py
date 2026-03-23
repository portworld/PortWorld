from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from json import JSONDecodeError
from typing import Protocol

from backend.core.settings import Settings
from backend.core.storage import BackendStorage, RealtimeReadOnlyStorageView
from backend.memory.lifecycle import USER_MEMORY_TEMPLATE
from backend.tools.contracts import ToolCall, ToolDefinition, ToolResult
from backend.tools.memory_candidates import MemoryCandidateToolExecutor
from backend.tools.memory import MemoryToolExecutor
from backend.tools.user_memory import UserMemoryToolExecutor
from backend.tools.providers.tavily import TavilySearchProvider
from backend.tools.registry import RealtimeToolRegistry, ToolRegistryError, UnknownToolError
from backend.tools.search import SearchProvider
from backend.tools.web_search import WebSearchToolExecutor

logger = logging.getLogger(__name__)
MAX_USER_MEMORY_INSTRUCTION_CHARS = 700


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
    registry: SearchProviderRegistry = field(
        default_factory=build_default_search_provider_registry
    )
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


@dataclass(frozen=True, slots=True)
class ToolCatalogContext:
    storage: RealtimeReadOnlyStorageView
    user_memory_storage: BackendStorage
    search_provider: SearchProvider | None
    web_search_provider: str | None
    web_search_max_results: int


class ToolCatalogContributor(Protocol):
    def __call__(
        self,
        *,
        registry: RealtimeToolRegistry,
        context: ToolCatalogContext,
    ) -> None: ...


def _register_memory_tools(
    *,
    registry: RealtimeToolRegistry,
    context: ToolCatalogContext,
) -> None:
    storage = context.storage
    registry.register(
        definition=ToolDefinition(
            name="get_short_term_memory",
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
            name="get_long_term_memory",
            description=(
                "Read the current long-term session memory for this active session."
            ),
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ),
        executor=MemoryToolExecutor(
            storage=storage,
            memory_scope="long_term",
        ),
    )
    registry.register(
        definition=ToolDefinition(
            name="get_cross_session_memory",
            description=(
                "Read the cross-session memory summary for this user."
            ),
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ),
        executor=MemoryToolExecutor(
            storage=storage,
            memory_scope="cross_session",
        ),
    )


def _register_web_search_tool(
    *,
    registry: RealtimeToolRegistry,
    context: ToolCatalogContext,
) -> None:
    if context.search_provider is None:
        return
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
            provider=context.search_provider,
            provider_name=context.web_search_provider or "tavily",
            max_results=context.web_search_max_results,
        ),
    )


def _register_user_memory_tools(
    *,
    registry: RealtimeToolRegistry,
    context: ToolCatalogContext,
) -> None:
    registry.register(
        definition=ToolDefinition(
            name="get_user_memory",
            description="Read the saved durable user memory for this user.",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ),
        executor=UserMemoryToolExecutor(storage=context.user_memory_storage, mode="get"),
    )
    registry.register(
        definition=ToolDefinition(
            name="update_user_memory",
            description=(
                "Update confirmed user memory facts. Omit fields that are still unknown."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "job": {"type": "string"},
                    "company": {"type": "string"},
                    "preferred_language": {"type": "string"},
                    "location": {"type": "string"},
                    "intended_use": {"type": "string"},
                    "preferences": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "projects": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "additionalProperties": False,
            },
        ),
        executor=UserMemoryToolExecutor(storage=context.user_memory_storage, mode="update"),
    )
    registry.register(
        definition=ToolDefinition(
            name="complete_user_memory_onboarding",
            description=(
                "Finish onboarding after the user has either shared enough profile context or explicitly chosen to skip the remaining questions."
            ),
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ),
        executor=UserMemoryToolExecutor(storage=context.user_memory_storage, mode="complete"),
    )
    registry.register(
        definition=ToolDefinition(
            name="capture_memory_candidate",
            description=(
                "Capture a concise durable-memory candidate from the current conversation. "
                "Use this implicitly when the user naturally reveals stable preferences, identity facts, or ongoing threads."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["user", "cross_session"],
                    },
                    "section_hint": {
                        "type": "string",
                        "enum": [
                            "identity",
                            "preferences",
                            "stable_facts",
                            "ongoing_threads",
                            "follow_ups",
                            "recent_facts",
                        ],
                    },
                    "fact": {"type": "string"},
                    "stability": {
                        "type": "string",
                        "enum": ["stable", "semi_stable"],
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                },
                "required": ["scope", "section_hint", "fact", "stability", "confidence"],
                "additionalProperties": False,
            },
        ),
        executor=MemoryCandidateToolExecutor(storage=context.user_memory_storage),
    )


DEFAULT_TOOL_CATALOG_CONTRIBUTORS: tuple[ToolCatalogContributor, ...] = (
    _register_memory_tools,
    _register_user_memory_tools,
    _register_web_search_tool,
)


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
        registry = RealtimeToolRegistry()
        for contributor in DEFAULT_TOOL_CATALOG_CONTRIBUTORS:
            contributor(registry=registry, context=context)
        return registry

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
            web_search_enabled=filtered_registry.has_tool("web_search"),
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
            return ToolResult(
                ok=False,
                name=call.name,
                call_id=call.call_id,
                payload={"session_id": call.session_id},
                error_code="TOOL_TIMEOUT",
                error_message=f"Tool execution timed out after {self.tool_timeout_ms}ms",
            )
        except UnknownToolError as exc:
            logger.warning(
                "Unknown tool requested session_id=%s call_id=%s name=%s",
                call.session_id,
                call.call_id,
                call.name,
                exc_info=exc,
            )
            return ToolResult(
                ok=False,
                name=call.name,
                call_id=call.call_id,
                payload={"session_id": call.session_id},
                error_code="UNKNOWN_TOOL",
                error_message="Unknown requested tool",
            )
        except ToolRegistryError as exc:
            logger.warning(
                "Tool execution failed due to registry error session_id=%s call_id=%s name=%s",
                call.session_id,
                call.call_id,
                call.name,
                exc_info=exc,
            )
            return ToolResult(
                ok=False,
                name=call.name,
                call_id=call.call_id,
                payload={"session_id": call.session_id},
                error_code="TOOL_EXECUTION_FAILED",
                error_message="Tool execution failed",
            )
        except Exception:  # pragma: no cover - defensive fallback
            logger.exception(
                "Unexpected tool execution failure session_id=%s call_id=%s name=%s",
                call.session_id,
                call.call_id,
                call.name,
            )
            return ToolResult(
                ok=False,
                name=call.name,
                call_id=call.call_id,
                payload={"session_id": call.session_id},
                error_code="TOOL_EXECUTION_FAILED",
                error_message="Tool execution failed",
            )

    def build_session_instructions(self, *, base_instructions: str) -> str:
        sections: list[str] = [base_instructions.rstrip()]

        tool_usage_block = self._build_tool_usage_block()
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

        user_memory_snippet = self._build_user_memory_instruction_snippet(user_memory_markdown)
        if user_memory_snippet:
            sections.append(
                "\n".join(["Stable user memory context:", user_memory_snippet])
            )

        return "\n\n".join(section for section in sections if section).strip() + "\n"

    def _build_tool_usage_block(self) -> str:
        guidance_lines = ["Tool usage policy:"]
        if self.registry.has_tool("get_short_term_memory"):
            guidance_lines.append(
                "- Use get_short_term_memory when the user asks about what is visible now or what was seen in the last few moments."
            )
        if self.registry.has_tool("get_long_term_memory"):
            guidance_lines.append(
                "- Use get_long_term_memory when the user asks about what has been seen across the current session."
            )
        if self.registry.has_tool("get_cross_session_memory"):
            guidance_lines.append(
                "- Use get_cross_session_memory when the user asks about durable context from prior sessions."
            )
        if self.registry.has_tool("update_user_memory"):
            guidance_lines.append(
                "- Use update_user_memory only for facts the user has clearly confirmed."
            )
        if self.registry.has_tool("complete_user_memory_onboarding"):
            guidance_lines.append(
                "- Use complete_user_memory_onboarding only when the onboarding interview is genuinely complete and the user is ready to move on, even if some questions were skipped."
            )
        if self.registry.has_tool("web_search"):
            guidance_lines.append(
                "- Use web_search only when the user explicitly asks for fresh external facts or documentation."
            )
        if self.registry.has_tool("capture_memory_candidate"):
            guidance_lines.extend(
                [
                    "- The saved USER memory is already loaded into your instructions; do not call a tool to reread it in normal conversation.",
                    "- When the user naturally reveals a stable preference, identity fact, intended use, or durable ongoing thread, capture it with capture_memory_candidate without asking the user to confirm memory behavior.",
                    "- Only capture concise facts that are likely to matter across sessions.",
                ]
            )
        if self.registry.has_tool("get_short_term_memory") or self.registry.has_tool(
            "get_long_term_memory"
        ):
            guidance_lines.extend(
                [
                    "- Do not claim visual context you have not retrieved through a tool.",
                    "- Do not ask for visual memory tools when the request does not depend on recent visual context.",
                ]
            )
        guidance_lines.extend(
            [
                "- Prefer one relevant tool call, then answer directly instead of chaining tools.",
                "- Keep answers concise after tool use.",
                "- Do not mention internal tool names or backend execution details to the user.",
            ]
        )
        return "\n".join(guidance_lines)

    @staticmethod
    def _build_user_memory_instruction_snippet(markdown: str) -> str:
        candidate = markdown.strip()
        if not candidate or candidate == USER_MEMORY_TEMPLATE.strip():
            return ""

        sections: list[str] = []
        current_header: str | None = None
        current_lines: list[str] = []
        for raw_line in candidate.splitlines():
            line = raw_line.rstrip()
            if line.startswith("## "):
                if current_header is not None:
                    rendered = RealtimeToolingRuntime._render_user_memory_section(
                        header=current_header,
                        lines=current_lines,
                    )
                    if rendered:
                        sections.append(rendered)
                current_header = line
                current_lines = []
                continue
            if current_header is not None:
                current_lines.append(line)
        if current_header is not None:
            rendered = RealtimeToolingRuntime._render_user_memory_section(
                header=current_header,
                lines=current_lines,
            )
            if rendered:
                sections.append(rendered)

        compact = "\n".join(sections).strip()
        if not compact:
            return ""
        if len(compact) <= MAX_USER_MEMORY_INSTRUCTION_CHARS:
            return compact
        return compact[: MAX_USER_MEMORY_INSTRUCTION_CHARS - 3].rstrip() + "..."

    @staticmethod
    def _render_user_memory_section(*, header: str, lines: list[str]) -> str:
        normalized_lines = [
            line.strip()
            for line in lines
            if line.strip() and line.strip().lower() != "- none"
        ]
        if not normalized_lines:
            return ""
        return "\n".join([header, *normalized_lines])

    async def startup(self) -> None:
        if self.search_provider is not None:
            await self.search_provider.startup()

    async def shutdown(self) -> None:
        if self.search_provider is not None:
            await self.search_provider.shutdown()
