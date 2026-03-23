from __future__ import annotations

from backend.memory.lifecycle import USER_MEMORY_TEMPLATE
from backend.tools.catalog import (
    TOOL_CAPTURE_MEMORY_CANDIDATE,
    TOOL_COMPLETE_USER_MEMORY_ONBOARDING,
    TOOL_GET_CROSS_SESSION_MEMORY,
    TOOL_GET_LONG_TERM_MEMORY,
    TOOL_GET_SHORT_TERM_MEMORY,
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
            "- Use get_cross_session_memory when the user asks about durable context from prior sessions."
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
