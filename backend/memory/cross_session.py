from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from backend.memory.lifecycle import CROSS_SESSION_MEMORY_TEMPLATE
from backend.memory.normalize import normalize_string, normalize_string_list

_MAX_SECTION_ITEMS = 8


def parse_cross_session_markdown(markdown_text: str) -> dict[str, list[str]]:
    sections = _split_sections(markdown_text)
    return {
        "active_themes": _extract_bullets(sections.get("Active Themes", ())),
        "ongoing_projects": _extract_bullets(sections.get("Ongoing Projects", ())),
        "important_recent_facts": _extract_bullets(sections.get("Important Recent Facts", ())),
        "follow_up_items": _extract_bullets(sections.get("Follow-Up Items", ())),
    }


def render_cross_session_markdown(payload: Mapping[str, object]) -> str:
    active_themes = normalize_string_list(payload.get("active_themes"))
    ongoing_projects = normalize_string_list(payload.get("ongoing_projects"))
    important_recent_facts = normalize_string_list(payload.get("important_recent_facts"))
    follow_up_items = normalize_string_list(payload.get("follow_up_items"))

    lines = [
        "# Cross-Session Memory",
        "",
        "## Active Themes",
    ]
    lines.extend(_render_bullets(active_themes))
    lines.extend(["", "## Ongoing Projects"])
    lines.extend(_render_bullets(ongoing_projects))
    lines.extend(["", "## Important Recent Facts"])
    lines.extend(_render_bullets(important_recent_facts))
    lines.extend(["", "## Follow-Up Items"])
    lines.extend(_render_bullets(follow_up_items))
    lines.append("")
    return "\n".join(lines)


def promote_session_memory_to_cross_session(
    *,
    existing_markdown: str | None,
    session_memory: Mapping[str, Any],
) -> str:
    existing = parse_cross_session_markdown(existing_markdown or CROSS_SESSION_MEMORY_TEMPLATE)

    active_themes = _merge_unique(
        existing.get("active_themes", ()),
        [
            normalize_string(session_memory.get("current_task_guess")),
        ],
    )
    ongoing_projects = _merge_unique(
        existing.get("ongoing_projects", ()),
        normalize_string_list(session_memory.get("documents_seen")),
    )
    important_recent_facts = _merge_unique(
        existing.get("important_recent_facts", ()),
        [
            normalize_string(session_memory.get("environment_summary")),
            *normalize_string_list(session_memory.get("notable_transitions")),
        ],
    )
    follow_up_items = _merge_unique(
        existing.get("follow_up_items", ()),
        normalize_string_list(session_memory.get("open_uncertainties")),
    )

    return render_cross_session_markdown(
        {
            "active_themes": active_themes,
            "ongoing_projects": ongoing_projects,
            "important_recent_facts": important_recent_facts,
            "follow_up_items": follow_up_items,
        }
    )


def _split_sections(markdown_text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current_section: str | None = None
    for line in markdown_text.splitlines():
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            current_section = match.group(1).strip()
            sections.setdefault(current_section, [])
            continue
        if current_section is None:
            continue
        sections[current_section].append(line)
    return sections


def _extract_bullets(lines: list[str] | tuple[str, ...]) -> list[str]:
    values: list[str] = []
    for line in lines:
        match = re.match(r"^\s*-\s*(.+?)\s*$", line)
        if not match:
            continue
        value = normalize_string(match.group(1))
        if not value or value.lower() == "none":
            continue
        values.append(value)
    return values


def _render_bullets(values: list[str]) -> list[str]:
    if not values:
        return ["- None"]
    return [f"- {value}" for value in values]


def _merge_unique(existing: list[str] | tuple[str, ...], new_values: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for value in [*existing, *new_values]:
        normalized = normalize_string(value)
        if not normalized:
            continue
        lowered = normalized.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        merged.append(normalized)
    return merged[-_MAX_SECTION_ITEMS:]
