from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from backend.core.storage import BackendStorage, RealtimeReadOnlyStorageView
from backend.tools.contracts import ToolDefinition, ToolExecutor
from backend.tools.memory import MemoryScope, MemoryToolExecutor
from backend.tools.memory_candidates import MemoryCandidateToolExecutor
from backend.tools.registry import RealtimeToolRegistry
from backend.tools.search import SearchProvider
from backend.tools.user_memory import UserMemoryMode, UserMemoryToolExecutor
from backend.tools.web_search import WebSearchToolExecutor

TOOL_GET_SHORT_TERM_MEMORY = "get_short_term_memory"
TOOL_GET_LONG_TERM_MEMORY = "get_long_term_memory"
TOOL_GET_CROSS_SESSION_MEMORY = "get_cross_session_memory"
TOOL_GET_USER_MEMORY = "get_user_memory"
TOOL_UPDATE_USER_MEMORY = "update_user_memory"
TOOL_COMPLETE_USER_MEMORY_ONBOARDING = "complete_user_memory_onboarding"
TOOL_CAPTURE_MEMORY_CANDIDATE = "capture_memory_candidate"
TOOL_WEB_SEARCH = "web_search"

DEFAULT_MODE_ALLOWED_TOOL_NAMES: frozenset[str] = frozenset(
    {
        TOOL_GET_SHORT_TERM_MEMORY,
        TOOL_GET_LONG_TERM_MEMORY,
        TOOL_GET_CROSS_SESSION_MEMORY,
        TOOL_CAPTURE_MEMORY_CANDIDATE,
        TOOL_WEB_SEARCH,
    }
)

USER_MEMORY_ONBOARDING_ALLOWED_TOOL_NAMES: frozenset[str] = frozenset(
    {
        TOOL_GET_USER_MEMORY,
        TOOL_UPDATE_USER_MEMORY,
        TOOL_COMPLETE_USER_MEMORY_ONBOARDING,
    }
)

_EMPTY_OBJECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

_WEB_SEARCH_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "The search query to run.",
        }
    },
    "required": ["query"],
    "additionalProperties": False,
}

_UPDATE_USER_MEMORY_INPUT_SCHEMA: dict[str, Any] = {
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
}

_CAPTURE_MEMORY_CANDIDATE_INPUT_SCHEMA: dict[str, Any] = {
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
}


@dataclass(frozen=True, slots=True)
class ToolCatalogContext:
    storage: RealtimeReadOnlyStorageView
    user_memory_storage: BackendStorage
    search_provider: SearchProvider | None
    web_search_provider: str | None
    web_search_max_results: int


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    build_executor: ToolExecutorBuilder


class ToolExecutorBuilder(Protocol):
    def __call__(self, context: ToolCatalogContext) -> ToolExecutor: ...


class ToolCatalogContributor(Protocol):
    def __call__(
        self,
        *,
        registry: RealtimeToolRegistry,
        context: ToolCatalogContext,
    ) -> None: ...


def _register_specs(
    *,
    registry: RealtimeToolRegistry,
    context: ToolCatalogContext,
    specs: tuple[ToolSpec, ...],
) -> None:
    for spec in specs:
        registry.register(
            definition=ToolDefinition(
                name=spec.name,
                description=spec.description,
                input_schema=spec.input_schema,
            ),
            executor=spec.build_executor(context),
        )


def _register_memory_tools(
    *,
    registry: RealtimeToolRegistry,
    context: ToolCatalogContext,
) -> None:
    _register_specs(
        registry=registry,
        context=context,
        specs=(
            ToolSpec(
                name=TOOL_GET_SHORT_TERM_MEMORY,
                description="Read the current short-term visual memory for this active session.",
                input_schema=_EMPTY_OBJECT_SCHEMA,
                build_executor=lambda ctx: MemoryToolExecutor(
                    storage=ctx.storage,
                    memory_scope=MemoryScope.SHORT_TERM,
                ),
            ),
            ToolSpec(
                name=TOOL_GET_LONG_TERM_MEMORY,
                description="Read the current long-term session memory for this active session.",
                input_schema=_EMPTY_OBJECT_SCHEMA,
                build_executor=lambda ctx: MemoryToolExecutor(
                    storage=ctx.storage,
                    memory_scope=MemoryScope.LONG_TERM,
                ),
            ),
            ToolSpec(
                name=TOOL_GET_CROSS_SESSION_MEMORY,
                description="Read the cross-session memory summary for this user.",
                input_schema=_EMPTY_OBJECT_SCHEMA,
                build_executor=lambda ctx: MemoryToolExecutor(
                    storage=ctx.storage,
                    memory_scope=MemoryScope.CROSS_SESSION,
                ),
            ),
        ),
    )


def _register_web_search_tool(
    *,
    registry: RealtimeToolRegistry,
    context: ToolCatalogContext,
) -> None:
    if context.search_provider is None:
        return
    _register_specs(
        registry=registry,
        context=context,
        specs=(
            ToolSpec(
                name=TOOL_WEB_SEARCH,
                description="Search the web for fresh external context.",
                input_schema=_WEB_SEARCH_INPUT_SCHEMA,
                build_executor=lambda ctx: WebSearchToolExecutor(
                    provider=ctx.search_provider,
                    provider_name=ctx.web_search_provider or "tavily",
                    max_results=ctx.web_search_max_results,
                ),
            ),
        ),
    )


def _register_user_memory_tools(
    *,
    registry: RealtimeToolRegistry,
    context: ToolCatalogContext,
) -> None:
    _register_specs(
        registry=registry,
        context=context,
        specs=(
            ToolSpec(
                name=TOOL_GET_USER_MEMORY,
                description="Read the saved durable user memory for this user.",
                input_schema=_EMPTY_OBJECT_SCHEMA,
                build_executor=lambda ctx: UserMemoryToolExecutor(
                    storage=ctx.user_memory_storage,
                    mode=UserMemoryMode.GET,
                ),
            ),
            ToolSpec(
                name=TOOL_UPDATE_USER_MEMORY,
                description="Update confirmed user memory facts. Omit fields that are still unknown.",
                input_schema=_UPDATE_USER_MEMORY_INPUT_SCHEMA,
                build_executor=lambda ctx: UserMemoryToolExecutor(
                    storage=ctx.user_memory_storage,
                    mode=UserMemoryMode.UPDATE,
                ),
            ),
            ToolSpec(
                name=TOOL_COMPLETE_USER_MEMORY_ONBOARDING,
                description=(
                    "Finish onboarding after the user has either shared enough profile context "
                    "or explicitly chosen to skip the remaining questions."
                ),
                input_schema=_EMPTY_OBJECT_SCHEMA,
                build_executor=lambda ctx: UserMemoryToolExecutor(
                    storage=ctx.user_memory_storage,
                    mode=UserMemoryMode.COMPLETE,
                ),
            ),
            ToolSpec(
                name=TOOL_CAPTURE_MEMORY_CANDIDATE,
                description=(
                    "Capture a concise durable-memory candidate from the current conversation. "
                    "Use this implicitly when the user naturally reveals stable preferences, "
                    "identity facts, or ongoing threads."
                ),
                input_schema=_CAPTURE_MEMORY_CANDIDATE_INPUT_SCHEMA,
                build_executor=lambda ctx: MemoryCandidateToolExecutor(
                    storage=ctx.user_memory_storage,
                ),
            ),
        ),
    )


DEFAULT_TOOL_CATALOG_CONTRIBUTORS: tuple[ToolCatalogContributor, ...] = (
    _register_memory_tools,
    _register_user_memory_tools,
    _register_web_search_tool,
)


def build_tool_registry(
    *,
    context: ToolCatalogContext,
    contributors: tuple[ToolCatalogContributor, ...] = DEFAULT_TOOL_CATALOG_CONTRIBUTORS,
) -> RealtimeToolRegistry:
    registry = RealtimeToolRegistry()
    for contributor in contributors:
        contributor(registry=registry, context=context)
    return registry
