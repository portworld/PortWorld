from __future__ import annotations

from backend.memory.lifecycle import USER_MEMORY_TEMPLATE
from backend.tools.catalog import (
    TOOL_DELEGATE_TO_OPENCLAW,
    TOOL_CAPTURE_MEMORY_CANDIDATE,
    TOOL_COMPLETE_USER_MEMORY_ONBOARDING,
    TOOL_GET_CROSS_SESSION_MEMORY,
    TOOL_GET_LONG_TERM_MEMORY,
    TOOL_OPENCLAW_TASK_CANCEL,
    TOOL_OPENCLAW_TASK_STATUS,
    TOOL_GET_SHORT_TERM_MEMORY,
    TOOL_MEMORY_V2_CORRECT_ITEM,
    TOOL_MEMORY_V2_DELETE_ITEM,
    TOOL_MEMORY_V2_GET_ITEM,
    TOOL_MEMORY_V2_GET_ITEM_EVIDENCE,
    TOOL_MEMORY_V2_GET_LIVE_BUNDLE,
    TOOL_MEMORY_V2_GET_CONFLICT_GROUP,
    TOOL_MEMORY_V2_LIST_ITEMS,
    TOOL_MEMORY_V2_LIST_CONFLICTS,
    TOOL_MEMORY_V2_MERGE_ITEMS,
    TOOL_MEMORY_V2_SUPPRESS_CONFLICT_SIDE,
    TOOL_MEMORY_V2_SUPPRESS_ITEM,
    TOOL_UPDATE_USER_MEMORY,
    TOOL_WEB_SEARCH,
)
from backend.tools.registry import RealtimeToolRegistry

MAX_USER_MEMORY_INSTRUCTION_CHARS = 700


def build_tool_usage_block(*, registry: RealtimeToolRegistry) -> str:
    guidance_lines = ["Tool usage policy:"]
    if registry.has_tool(TOOL_GET_SHORT_TERM_MEMORY):
        guidance_lines.append(
            "- Use get_short_term_memory when the user asks about what is visible now or what was seen in the last few moments."
        )
    if registry.has_tool(TOOL_GET_LONG_TERM_MEMORY):
        guidance_lines.append(
            "- Use get_long_term_memory when the user asks about what has been seen across the current session."
        )
    if registry.has_tool(TOOL_GET_CROSS_SESSION_MEMORY):
        guidance_lines.append(
            "- Use get_cross_session_memory only when the user explicitly wants legacy markdown memory views for compatibility, debugging, or export-like inspection."
        )
    if registry.has_tool(TOOL_MEMORY_V2_LIST_ITEMS):
        guidance_lines.append(
            "- Use memory_v2_list_items when you need a broad inventory of durable memory items after memory_v2_get_live_bundle."
        )
    if registry.has_tool(TOOL_MEMORY_V2_GET_LIVE_BUNDLE):
        guidance_lines.append(
            "- Prefer memory_v2_get_live_bundle as the default first read for durable-memory questions; it returns ranked, evidence-backed context."
        )
    if registry.has_tool(TOOL_MEMORY_V2_GET_ITEM):
        guidance_lines.append(
            "- Use memory_v2_get_item when you need the structured details for one durable memory item."
        )
    if registry.has_tool(TOOL_MEMORY_V2_GET_ITEM_EVIDENCE):
        guidance_lines.append(
            "- Use memory_v2_get_item_evidence when provenance matters before relying on a remembered fact."
        )
    if registry.has_tool(TOOL_MEMORY_V2_LIST_CONFLICTS):
        guidance_lines.append(
            "- Use memory_v2_list_conflicts when the user wants to inspect memories that disagree and need explicit resolution."
        )
    if registry.has_tool(TOOL_MEMORY_V2_GET_CONFLICT_GROUP):
        guidance_lines.append(
            "- Use memory_v2_get_conflict_group to inspect the competing items inside one conflict group before taking action."
        )
    if registry.has_tool(TOOL_MEMORY_V2_MERGE_ITEMS):
        guidance_lines.append(
            "- Use memory_v2_merge_items only on explicit user intent to merge two conflicting durable memories."
        )
    if registry.has_tool(TOOL_MEMORY_V2_SUPPRESS_CONFLICT_SIDE):
        guidance_lines.append(
            "- Use memory_v2_suppress_conflict_side only on explicit user intent to suppress one side of a conflict."
        )
    if registry.has_tool(TOOL_MEMORY_V2_CORRECT_ITEM):
        guidance_lines.append(
            "- Use memory_v2_correct_item only when the user is explicitly correcting or refining something already remembered."
        )
    if registry.has_tool(TOOL_MEMORY_V2_SUPPRESS_ITEM):
        guidance_lines.append(
            "- Use memory_v2_suppress_item when the user wants a remembered item kept out of use without fully deleting it."
        )
    if registry.has_tool(TOOL_MEMORY_V2_DELETE_ITEM):
        guidance_lines.append(
            "- Use memory_v2_delete_item only when the user explicitly wants a remembered item removed."
        )
    if registry.has_tool(TOOL_UPDATE_USER_MEMORY):
        guidance_lines.append(
            "- Use update_user_memory only for facts the user has clearly confirmed."
        )
    if registry.has_tool(TOOL_COMPLETE_USER_MEMORY_ONBOARDING):
        guidance_lines.append(
            "- Use complete_user_memory_onboarding only when the onboarding interview is genuinely complete and the user is ready to move on, even if some questions were skipped."
        )
    if registry.has_tool(TOOL_WEB_SEARCH):
        guidance_lines.append(
            "- Use web_search only when the user explicitly asks for fresh external facts or documentation."
        )
    if registry.has_tool(TOOL_DELEGATE_TO_OPENCLAW):
        guidance_lines.append(
            "- Use delegate_to_openclaw for long-running delegated tasks and then check progress with openclaw_task_status."
        )
    if registry.has_tool(TOOL_OPENCLAW_TASK_CANCEL):
        guidance_lines.append(
            "- Use openclaw_task_cancel only when the user explicitly asks to stop a delegated task."
        )
    if registry.has_tool(TOOL_OPENCLAW_TASK_STATUS):
        guidance_lines.append(
            "- Use openclaw_task_status to poll delegated task progress after delegate_to_openclaw."
        )
    if registry.has_tool(TOOL_CAPTURE_MEMORY_CANDIDATE):
        guidance_lines.extend(
            [
                "- The saved USER memory is already loaded into your instructions; do not call a tool to reread it in normal conversation.",
                "- When the user naturally reveals a stable preference, identity fact, intended use, or durable ongoing thread, capture it with capture_memory_candidate without asking the user to confirm memory behavior.",
                "- Only capture concise facts that are likely to matter across sessions.",
            ]
        )
    if registry.has_tool(TOOL_GET_SHORT_TERM_MEMORY) or registry.has_tool(TOOL_GET_LONG_TERM_MEMORY):
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


def build_memory_retrieval_preference_block(*, registry: RealtimeToolRegistry) -> str:
    lines = ["Memory retrieval preference order:"]

    if registry.has_tool(TOOL_MEMORY_V2_GET_LIVE_BUNDLE):
        lines.append(
            "- For durable-memory usefulness questions, call memory_v2_get_live_bundle first."
        )
    if registry.has_tool(TOOL_MEMORY_V2_GET_ITEM):
        lines.append(
            "- Use memory_v2_get_item only after the live bundle when the answer depends on one specific item."
        )
    if registry.has_tool(TOOL_MEMORY_V2_GET_ITEM_EVIDENCE):
        lines.append(
            "- Use memory_v2_get_item_evidence when the user asks if a memory is well-supported or where it came from."
        )
    if registry.has_tool(TOOL_GET_SHORT_TERM_MEMORY):
        lines.append(
            "- Use get_short_term_memory for immediate visual recency (what is visible now or what happened in the last moments)."
        )
    if registry.has_tool(TOOL_GET_LONG_TERM_MEMORY):
        lines.append(
            "- Use get_long_term_memory for broader within-session visual context."
        )
    if registry.has_tool(TOOL_GET_CROSS_SESSION_MEMORY):
        lines.append(
            "- Use get_cross_session_memory only for markdown compatibility/debug/export requests, not as the default durable-memory lookup."
        )

    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def build_user_memory_instruction_snippet(markdown: str) -> str:
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
                rendered = _render_user_memory_section(
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
        rendered = _render_user_memory_section(
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


def _render_user_memory_section(*, header: str, lines: list[str]) -> str:
    normalized_lines = [
        line.strip()
        for line in lines
        if line.strip() and line.strip().lower() != "- none"
    ]
    if not normalized_lines:
        return ""
    return "\n".join([header, *normalized_lines])
