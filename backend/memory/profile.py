from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from backend.memory.lifecycle import (
    PROFILE_ALLOWLISTED_FIELDS,
    PROFILE_METADATA_KEY,
    PROFILE_SCHEMA_VERSION,
    ProfileLifecycleMetadata,
    ProfileRecord,
)

PROFILE_MARKDOWN_HEADER = "# User Profile\n\n"


def parse_profile_record(payload: Mapping[str, object]) -> ProfileRecord:
    metadata_payload = payload.get(PROFILE_METADATA_KEY)
    metadata = ProfileLifecycleMetadata()
    if isinstance(metadata_payload, Mapping):
        updated_at_ms = _coerce_optional_int(metadata_payload.get("updated_at_ms"))
        source = _normalize_optional_string(metadata_payload.get("source"))
        schema_version_raw = metadata_payload.get("schema_version")
        schema_version = (
            schema_version_raw.strip()
            if isinstance(schema_version_raw, str) and schema_version_raw.strip()
            else PROFILE_SCHEMA_VERSION
        )
        metadata = ProfileLifecycleMetadata(
            schema_version=schema_version,
            updated_at_ms=updated_at_ms,
            source=source,
        )

    return ProfileRecord(
        name=_normalize_optional_string(payload.get("name")),
        job=_normalize_optional_string(payload.get("job")),
        company=_normalize_optional_string(payload.get("company")),
        preferences=_normalize_string_list(payload.get("preferences")),
        projects=_normalize_string_list(payload.get("projects")),
        metadata=metadata,
    )


def build_profile_payload(
    record: ProfileRecord,
    *,
    include_metadata: bool = True,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for field_name in PROFILE_ALLOWLISTED_FIELDS:
        value = getattr(record, field_name)
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                payload[field_name] = normalized
            continue
        if isinstance(value, list):
            if value:
                payload[field_name] = list(value)

    if include_metadata and payload:
        metadata_payload: dict[str, Any] = {
            "schema_version": record.metadata.schema_version or PROFILE_SCHEMA_VERSION,
        }
        if record.metadata.updated_at_ms is not None:
            metadata_payload["updated_at_ms"] = record.metadata.updated_at_ms
        if record.metadata.source:
            metadata_payload["source"] = record.metadata.source
        payload[PROFILE_METADATA_KEY] = metadata_payload
    return payload


def render_profile_markdown(record: ProfileRecord) -> str:
    lines = [
        "# User Profile",
        "",
    ]

    if record.name:
        lines.append(f"Name: {record.name}")
    if record.job:
        lines.append(f"Job: {record.job}")
    if record.company:
        lines.append(f"Company: {record.company}")
    if record.preferences:
        lines.append(f"Preferences: {', '.join(record.preferences)}")
    if record.projects:
        lines.append(f"Projects: {', '.join(record.projects)}")

    if len(lines) == 2:
        lines.append("No profile facts captured yet.")

    if record.metadata.updated_at_ms is not None or record.metadata.source:
        lines.extend(
            [
                "",
                "Metadata:",
                f"- Updated At Ms: {record.metadata.updated_at_ms if record.metadata.updated_at_ms is not None else 'unknown'}",
                f"- Source: {record.metadata.source or 'unknown'}",
            ]
        )

    lines.append("")
    return "\n".join(lines)


def build_profile_record(
    payload: Mapping[str, object],
    *,
    updated_at_ms: int | None,
    source: str | None,
) -> ProfileRecord:
    existing = parse_profile_record(payload)
    metadata = ProfileLifecycleMetadata(
        schema_version=PROFILE_SCHEMA_VERSION,
        updated_at_ms=updated_at_ms,
        source=_normalize_optional_string(source),
    )
    return ProfileRecord(
        name=existing.name,
        job=existing.job,
        company=existing.company,
        preferences=existing.preferences,
        projects=existing.projects,
        metadata=metadata,
    )


def empty_profile_payload() -> dict[str, Any]:
    return {}


def empty_profile_markdown() -> str:
    return PROFILE_MARKDOWN_HEADER


def _normalize_optional_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        candidate = item.strip()
        if not candidate:
            continue
        lowered = candidate.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(candidate)
    return normalized


def _coerce_optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


__all__ = [
    "PROFILE_MARKDOWN_HEADER",
    "build_profile_payload",
    "build_profile_record",
    "empty_profile_markdown",
    "empty_profile_payload",
    "parse_profile_record",
    "render_profile_markdown",
]
