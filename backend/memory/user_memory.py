from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from backend.memory.lifecycle import (
    USER_MEMORY_ALLOWLISTED_FIELDS,
    USER_MEMORY_METADATA_KEY,
    USER_MEMORY_SCHEMA_VERSION,
    USER_MEMORY_TEMPLATE,
    UserMemoryLifecycleMetadata,
    UserMemoryRecord,
)
from backend.memory.normalize import normalize_optional_string, normalize_string

USER_MEMORY_MARKDOWN_HEADER = "# User\n\n"


def parse_user_memory_record(payload: Mapping[str, object]) -> UserMemoryRecord:
    metadata_payload = payload.get(USER_MEMORY_METADATA_KEY)
    metadata = UserMemoryLifecycleMetadata()
    if isinstance(metadata_payload, Mapping):
        updated_at_ms = _coerce_optional_int(metadata_payload.get("updated_at_ms"))
        source = normalize_optional_string(metadata_payload.get("source"))
        schema_version_raw = metadata_payload.get("schema_version")
        schema_version = (
            schema_version_raw.strip()
            if isinstance(schema_version_raw, str) and schema_version_raw.strip()
            else USER_MEMORY_SCHEMA_VERSION
        )
        metadata = UserMemoryLifecycleMetadata(
            schema_version=schema_version,
            updated_at_ms=updated_at_ms,
            source=source,
        )

    return UserMemoryRecord(
        name=normalize_optional_string(payload.get("name")),
        job=normalize_optional_string(payload.get("job")),
        company=normalize_optional_string(payload.get("company")),
        preferred_language=normalize_optional_string(payload.get("preferred_language")),
        location=normalize_optional_string(payload.get("location")),
        intended_use=normalize_optional_string(payload.get("intended_use")),
        preferences=_normalize_string_list(payload.get("preferences")),
        projects=_normalize_string_list(payload.get("projects")),
        metadata=metadata,
    )


def build_user_memory_payload(
    record: UserMemoryRecord,
    *,
    include_metadata: bool = True,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for field_name in USER_MEMORY_ALLOWLISTED_FIELDS:
        value = getattr(record, field_name)
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                payload[field_name] = normalized
            continue
        if isinstance(value, list) and value:
            payload[field_name] = list(value)

    if include_metadata and payload:
        metadata_payload: dict[str, Any] = {
            "schema_version": record.metadata.schema_version or USER_MEMORY_SCHEMA_VERSION,
        }
        if record.metadata.updated_at_ms is not None:
            metadata_payload["updated_at_ms"] = record.metadata.updated_at_ms
        if record.metadata.source:
            metadata_payload["source"] = record.metadata.source
        payload[USER_MEMORY_METADATA_KEY] = metadata_payload
    return payload


def render_user_memory_markdown(record: UserMemoryRecord) -> str:
    lines = [
        "# User",
        "",
        "## Identity",
    ]
    if record.name:
        lines.append(f"- Name: {record.name}")
    if record.preferred_language:
        lines.append(f"- Preferred Language: {record.preferred_language}")
    if record.location:
        lines.append(f"- Location: {record.location}")
    lines.extend(["", "## Preferences"])
    if record.preferences:
        lines.extend(f"- {item}" for item in record.preferences)
    else:
        lines.append("- None")

    lines.extend(["", "## Stable Facts"])
    if record.job:
        lines.append(f"- Job: {record.job}")
    if record.company:
        lines.append(f"- Company: {record.company}")
    if record.intended_use:
        lines.append(f"- Intended Use: {record.intended_use}")
    if record.projects:
        lines.extend(f"- Project: {item}" for item in record.projects)
    elif not any([record.job, record.company, record.intended_use]):
        lines.append("- None")

    lines.extend(["", "## Open Questions", "- None", ""])
    return "\n".join(lines)


def build_user_memory_record(
    payload: Mapping[str, object],
    *,
    updated_at_ms: int | None,
    source: str | None,
) -> UserMemoryRecord:
    existing = parse_user_memory_record(payload)
    metadata = UserMemoryLifecycleMetadata(
        schema_version=USER_MEMORY_SCHEMA_VERSION,
        updated_at_ms=updated_at_ms,
        source=normalize_optional_string(source),
    )
    return UserMemoryRecord(
        name=existing.name,
        job=existing.job,
        company=existing.company,
        preferred_language=existing.preferred_language,
        location=existing.location,
        intended_use=existing.intended_use,
        preferences=existing.preferences,
        projects=existing.projects,
        metadata=metadata,
    )


def empty_user_memory_payload() -> dict[str, Any]:
    return {}


def empty_user_memory_markdown() -> str:
    return USER_MEMORY_TEMPLATE


def parse_user_memory_markdown(markdown_text: str) -> UserMemoryRecord:
    sections = _split_sections(markdown_text)
    identity = _extract_key_values(sections.get("Identity", ()))
    stable = _extract_key_values(sections.get("Stable Facts", ()))
    preferences = _extract_bullets(sections.get("Preferences", ()))
    projects = [
        value
        for key, value in _extract_key_value_items(sections.get("Stable Facts", ()))
        if key == "project"
    ]
    return UserMemoryRecord(
        name=normalize_optional_string(identity.get("name")),
        preferred_language=normalize_optional_string(identity.get("preferred language")),
        location=normalize_optional_string(identity.get("location")),
        job=normalize_optional_string(stable.get("job")),
        company=normalize_optional_string(stable.get("company")),
        intended_use=normalize_optional_string(stable.get("intended use")),
        preferences=preferences,
        projects=projects,
    )


def _normalize_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        candidate = normalize_string(item)
        if not candidate:
            continue
        lowered = candidate.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(candidate)
    return normalized


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


def _extract_bullets(lines: list[str]) -> list[str]:
    values: list[str] = []
    for line in lines:
        match = re.match(r"^\s*-\s*(.+?)\s*$", line)
        if not match:
            continue
        value = normalize_string(match.group(1))
        if not value or value.lower() == "none":
            continue
        if ":" in value:
            continue
        values.append(value)
    return values


def _extract_key_values(lines: list[str]) -> dict[str, str]:
    return {key: value for key, value in _extract_key_value_items(lines)}


def _extract_key_value_items(lines: list[str]) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    for line in lines:
        match = re.match(r"^\s*-\s*([^:]+):\s*(.+?)\s*$", line)
        if not match:
            continue
        key = normalize_string(match.group(1)).lower()
        value = normalize_string(match.group(2))
        if not key or not value or value.lower() == "none":
            continue
        values.append((key, value))
    return values


def _coerce_optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


# Compatibility exports while imports migrate from profile naming.
PROFILE_MARKDOWN_HEADER = USER_MEMORY_MARKDOWN_HEADER
parse_profile_record = parse_user_memory_record
build_profile_payload = build_user_memory_payload
render_profile_markdown = render_user_memory_markdown
build_profile_record = build_user_memory_record
empty_profile_payload = empty_user_memory_payload
empty_profile_markdown = empty_user_memory_markdown
parse_profile_markdown = parse_user_memory_markdown


__all__ = [
    "USER_MEMORY_MARKDOWN_HEADER",
    "USER_MEMORY_ALLOWLISTED_FIELDS",
    "build_user_memory_payload",
    "build_user_memory_record",
    "empty_user_memory_markdown",
    "empty_user_memory_payload",
    "parse_user_memory_markdown",
    "parse_user_memory_record",
    "render_user_memory_markdown",
    "PROFILE_MARKDOWN_HEADER",
    "build_profile_payload",
    "build_profile_record",
    "empty_profile_markdown",
    "empty_profile_payload",
    "parse_profile_markdown",
    "parse_profile_record",
    "render_profile_markdown",
]
