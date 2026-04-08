from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from backend.core.storage import BackendStorage, RealtimeReadOnlyStorageView
from backend.tools.contracts import ToolDefinition, ToolExecutor
from backend.tools.memory import MemoryScope, MemoryToolExecutor, MemoryV2ToolExecutor, MemoryV2ToolMode
from backend.tools.memory_candidates import MemoryCandidateToolExecutor
from backend.tools.openclaw import (
    DelegateToOpenClawToolExecutor,
    OpenClawTaskCancelToolExecutor,
    OpenClawTaskStatusToolExecutor,
)
from backend.tools.openclaw_runtime import OpenClawDelegationRuntime
from backend.tools.registry import RealtimeToolRegistry
from backend.tools.search import SearchProvider
from backend.tools.user_memory import UserMemoryMode, UserMemoryToolExecutor
from backend.tools.web_search import WebSearchToolExecutor

TOOL_GET_SHORT_TERM_MEMORY = "get_short_term_memory"
TOOL_GET_LONG_TERM_MEMORY = "get_long_term_memory"
TOOL_GET_CROSS_SESSION_MEMORY = "get_cross_session_memory"
TOOL_MEMORY_V2_LIST_ITEMS = "memory_v2_list_items"
TOOL_MEMORY_V2_GET_ITEM = "memory_v2_get_item"
TOOL_MEMORY_V2_GET_ITEM_EVIDENCE = "memory_v2_get_item_evidence"
TOOL_MEMORY_V2_GET_LIVE_BUNDLE = "memory_v2_get_live_bundle"
TOOL_MEMORY_V2_LIST_CONFLICTS = "memory_v2_list_conflicts"
TOOL_MEMORY_V2_GET_CONFLICT_GROUP = "memory_v2_get_conflict_group"
TOOL_MEMORY_V2_MERGE_ITEMS = "memory_v2_merge_items"
TOOL_MEMORY_V2_SUPPRESS_CONFLICT_SIDE = "memory_v2_suppress_conflict_side"
TOOL_MEMORY_V2_CORRECT_ITEM = "memory_v2_correct_item"
TOOL_MEMORY_V2_SUPPRESS_ITEM = "memory_v2_suppress_item"
TOOL_MEMORY_V2_DELETE_ITEM = "memory_v2_delete_item"
TOOL_GET_USER_MEMORY = "get_user_memory"
TOOL_UPDATE_USER_MEMORY = "update_user_memory"
TOOL_COMPLETE_USER_MEMORY_ONBOARDING = "complete_user_memory_onboarding"
TOOL_CAPTURE_MEMORY_CANDIDATE = "capture_memory_candidate"
TOOL_WEB_SEARCH = "web_search"
TOOL_DELEGATE_TO_OPENCLAW = "delegate_to_openclaw"
TOOL_OPENCLAW_TASK_STATUS = "openclaw_task_status"
TOOL_OPENCLAW_TASK_CANCEL = "openclaw_task_cancel"

DEFAULT_MODE_ALLOWED_TOOL_NAMES: frozenset[str] = frozenset(
    {
        TOOL_GET_SHORT_TERM_MEMORY,
        TOOL_GET_LONG_TERM_MEMORY,
        TOOL_GET_CROSS_SESSION_MEMORY,
        TOOL_MEMORY_V2_LIST_ITEMS,
        TOOL_MEMORY_V2_GET_ITEM,
        TOOL_MEMORY_V2_GET_ITEM_EVIDENCE,
        TOOL_MEMORY_V2_GET_LIVE_BUNDLE,
        TOOL_MEMORY_V2_LIST_CONFLICTS,
        TOOL_MEMORY_V2_GET_CONFLICT_GROUP,
        TOOL_MEMORY_V2_MERGE_ITEMS,
        TOOL_MEMORY_V2_SUPPRESS_CONFLICT_SIDE,
        TOOL_MEMORY_V2_CORRECT_ITEM,
        TOOL_MEMORY_V2_SUPPRESS_ITEM,
        TOOL_MEMORY_V2_DELETE_ITEM,
        TOOL_CAPTURE_MEMORY_CANDIDATE,
        TOOL_WEB_SEARCH,
        TOOL_DELEGATE_TO_OPENCLAW,
        TOOL_OPENCLAW_TASK_STATUS,
        TOOL_OPENCLAW_TASK_CANCEL,
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

_DELEGATE_TO_OPENCLAW_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task": {
            "type": "string",
            "description": "Task instructions for the delegated OpenClaw agent.",
        },
        "context": {
            "type": "object",
            "description": "Optional structured context for the delegated task.",
            "additionalProperties": True,
        },
        "agent_id": {
            "type": "string",
            "description": "Optional OpenClaw agent id override for this task.",
        },
    },
    "required": ["task"],
    "additionalProperties": False,
}

_MEMORY_V2_LIST_ITEMS_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "scope": {"type": "string"},
        "memory_class": {"type": "string"},
        "status": {"type": "string"},
        "tag": {"type": "string"},
        "session_id": {"type": "string"},
        "limit": {"type": "integer", "minimum": 0},
    },
    "additionalProperties": False,
}

_MEMORY_V2_ITEM_ID_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "item_id": {"type": "string"},
    },
    "required": ["item_id"],
    "additionalProperties": False,
}

_MEMORY_V2_GET_LIVE_BUNDLE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "session_id": {"type": "string"},
        "query_text": {"type": "string"},
        "intention_text": {"type": "string"},
        "memory_classes": {
            "type": "array",
            "items": {"type": "string"},
        },
        "statuses": {
            "type": "array",
            "items": {"type": "string"},
        },
        "limit": {"type": "integer", "minimum": 0},
        "evidence_limit_per_item": {"type": "integer", "minimum": 0},
    },
    "additionalProperties": False,
}

_MEMORY_V2_LIST_CONFLICTS_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "limit": {"type": "integer", "minimum": 0},
    },
    "additionalProperties": False,
}

_MEMORY_V2_GET_CONFLICT_GROUP_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "group_key": {"type": "string"},
    },
    "required": ["group_key"],
    "additionalProperties": False,
}

_MEMORY_V2_MERGE_ITEMS_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "target_item_id": {"type": "string"},
        "source_item_id": {"type": "string"},
        "reason": {"type": "string"},
        "actor": {"type": "string"},
        "suppress_source": {"type": "boolean"},
    },
    "required": ["target_item_id", "source_item_id", "reason"],
    "additionalProperties": False,
}

_MEMORY_V2_SUPPRESS_CONFLICT_SIDE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "item_id": {"type": "string"},
        "reason": {"type": "string"},
        "actor": {"type": "string"},
    },
    "required": ["item_id", "reason"],
    "additionalProperties": False,
}

_MEMORY_V2_CORRECT_ITEM_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "item_id": {"type": "string"},
        "summary": {"type": "string"},
        "structured_value": {
            "type": "object",
            "additionalProperties": True,
        },
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "relevance": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "maturity": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "tags": {"type": "array", "items": {"type": "string"}},
        "correction_note": {"type": "string"},
        "session_id": {"type": "string"},
        "status": {"type": "string"},
    },
    "required": ["item_id"],
    "additionalProperties": False,
}

_MEMORY_V2_SUPPRESS_ITEM_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "item_id": {"type": "string"},
        "note": {"type": "string"},
    },
    "required": ["item_id"],
    "additionalProperties": False,
}

_OPENCLAW_TASK_STATUS_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task_id": {
            "type": "string",
            "description": "OpenClaw task id returned by delegate_to_openclaw.",
        }
    },
    "required": ["task_id"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class ToolCatalogContext:
    storage: RealtimeReadOnlyStorageView
    user_memory_storage: BackendStorage
    search_provider: SearchProvider | None
    web_search_provider: str | None
    web_search_max_results: int
    openclaw_runtime: OpenClawDelegationRuntime | None


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
            ToolSpec(
                name=TOOL_MEMORY_V2_LIST_ITEMS,
                description="List canonical Memory v2 items with optional filters.",
                input_schema=_MEMORY_V2_LIST_ITEMS_INPUT_SCHEMA,
                build_executor=lambda ctx: MemoryV2ToolExecutor(
                    storage=ctx.user_memory_storage,
                    mode=MemoryV2ToolMode.LIST_ITEMS,
                ),
            ),
            ToolSpec(
                name=TOOL_MEMORY_V2_GET_ITEM,
                description="Get a canonical Memory v2 item by id.",
                input_schema=_MEMORY_V2_ITEM_ID_INPUT_SCHEMA,
                build_executor=lambda ctx: MemoryV2ToolExecutor(
                    storage=ctx.user_memory_storage,
                    mode=MemoryV2ToolMode.GET_ITEM,
                ),
            ),
            ToolSpec(
                name=TOOL_MEMORY_V2_GET_ITEM_EVIDENCE,
                description="Inspect evidence linked to a canonical Memory v2 item.",
                input_schema=_MEMORY_V2_ITEM_ID_INPUT_SCHEMA,
                build_executor=lambda ctx: MemoryV2ToolExecutor(
                    storage=ctx.user_memory_storage,
                    mode=MemoryV2ToolMode.GET_ITEM_EVIDENCE,
                ),
            ),
            ToolSpec(
                name=TOOL_MEMORY_V2_GET_LIVE_BUNDLE,
                description=(
                    "Get a ranked live bundle of durable Memory v2 items for what is most useful now, "
                    "including evidence summaries and ranking metadata."
                ),
                input_schema=_MEMORY_V2_GET_LIVE_BUNDLE_INPUT_SCHEMA,
                build_executor=lambda ctx: MemoryV2ToolExecutor(
                    storage=ctx.user_memory_storage,
                    mode=MemoryV2ToolMode.GET_LIVE_BUNDLE,
                ),
            ),
            ToolSpec(
                name=TOOL_MEMORY_V2_LIST_CONFLICTS,
                description="List Memory v2 conflict groups that require explicit merge or suppression actions.",
                input_schema=_MEMORY_V2_LIST_CONFLICTS_INPUT_SCHEMA,
                build_executor=lambda ctx: MemoryV2ToolExecutor(
                    storage=ctx.user_memory_storage,
                    mode=MemoryV2ToolMode.LIST_CONFLICTS,
                ),
            ),
            ToolSpec(
                name=TOOL_MEMORY_V2_GET_CONFLICT_GROUP,
                description="Inspect a specific Memory v2 conflict group by key.",
                input_schema=_MEMORY_V2_GET_CONFLICT_GROUP_INPUT_SCHEMA,
                build_executor=lambda ctx: MemoryV2ToolExecutor(
                    storage=ctx.user_memory_storage,
                    mode=MemoryV2ToolMode.GET_CONFLICT_GROUP,
                ),
            ),
            ToolSpec(
                name=TOOL_MEMORY_V2_MERGE_ITEMS,
                description=(
                    "Explicitly merge a source Memory v2 item into a target conflict-side item. "
                    "This is a write action and requires a reason."
                ),
                input_schema=_MEMORY_V2_MERGE_ITEMS_INPUT_SCHEMA,
                build_executor=lambda ctx: MemoryV2ToolExecutor(
                    storage=ctx.user_memory_storage,
                    mode=MemoryV2ToolMode.MERGE_ITEMS,
                ),
            ),
            ToolSpec(
                name=TOOL_MEMORY_V2_SUPPRESS_CONFLICT_SIDE,
                description=(
                    "Explicitly suppress one side of a Memory v2 conflict group. "
                    "This is a write action and requires a reason."
                ),
                input_schema=_MEMORY_V2_SUPPRESS_CONFLICT_SIDE_INPUT_SCHEMA,
                build_executor=lambda ctx: MemoryV2ToolExecutor(
                    storage=ctx.user_memory_storage,
                    mode=MemoryV2ToolMode.SUPPRESS_CONFLICT_SIDE,
                ),
            ),
            ToolSpec(
                name=TOOL_MEMORY_V2_CORRECT_ITEM,
                description="Correct or update a canonical Memory v2 item.",
                input_schema=_MEMORY_V2_CORRECT_ITEM_INPUT_SCHEMA,
                build_executor=lambda ctx: MemoryV2ToolExecutor(
                    storage=ctx.user_memory_storage,
                    mode=MemoryV2ToolMode.CORRECT_ITEM,
                ),
            ),
            ToolSpec(
                name=TOOL_MEMORY_V2_SUPPRESS_ITEM,
                description="Suppress a canonical Memory v2 item while preserving history.",
                input_schema=_MEMORY_V2_SUPPRESS_ITEM_INPUT_SCHEMA,
                build_executor=lambda ctx: MemoryV2ToolExecutor(
                    storage=ctx.user_memory_storage,
                    mode=MemoryV2ToolMode.SUPPRESS_ITEM,
                ),
            ),
            ToolSpec(
                name=TOOL_MEMORY_V2_DELETE_ITEM,
                description="Delete a canonical Memory v2 item.",
                input_schema=_MEMORY_V2_ITEM_ID_INPUT_SCHEMA,
                build_executor=lambda ctx: MemoryV2ToolExecutor(
                    storage=ctx.user_memory_storage,
                    mode=MemoryV2ToolMode.DELETE_ITEM,
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


def _register_openclaw_tools(
    *,
    registry: RealtimeToolRegistry,
    context: ToolCatalogContext,
) -> None:
    if context.openclaw_runtime is None:
        return
    _register_specs(
        registry=registry,
        context=context,
        specs=(
            ToolSpec(
                name=TOOL_DELEGATE_TO_OPENCLAW,
                description=(
                    "Delegate a longer-running task to the configured OpenClaw agent. "
                    "Returns immediately with task_id."
                ),
                input_schema=_DELEGATE_TO_OPENCLAW_INPUT_SCHEMA,
                build_executor=lambda ctx: DelegateToOpenClawToolExecutor(
                    runtime=ctx.openclaw_runtime,
                ),
            ),
            ToolSpec(
                name=TOOL_OPENCLAW_TASK_STATUS,
                description="Check the status of a delegated OpenClaw task.",
                input_schema=_OPENCLAW_TASK_STATUS_INPUT_SCHEMA,
                build_executor=lambda ctx: OpenClawTaskStatusToolExecutor(
                    runtime=ctx.openclaw_runtime,
                ),
            ),
            ToolSpec(
                name=TOOL_OPENCLAW_TASK_CANCEL,
                description="Cancel a delegated OpenClaw task.",
                input_schema=_OPENCLAW_TASK_STATUS_INPUT_SCHEMA,
                build_executor=lambda ctx: OpenClawTaskCancelToolExecutor(
                    runtime=ctx.openclaw_runtime,
                ),
            ),
        ),
    )


DEFAULT_TOOL_CATALOG_CONTRIBUTORS: tuple[ToolCatalogContributor, ...] = (
    _register_memory_tools,
    _register_user_memory_tools,
    _register_web_search_tool,
    _register_openclaw_tools,
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
