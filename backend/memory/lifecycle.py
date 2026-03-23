from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final


USER_MEMORY_SCHEMA_VERSION: Final[str] = "2"
MEMORY_EXPORT_SCHEMA_VERSION: Final[str] = "1"
DEFAULT_SESSION_MEMORY_RETENTION_DAYS: Final[int] = 30
USER_MEMORY_METADATA_KEY: Final[str] = "user_memory_metadata"

USER_MEMORY_ALLOWLISTED_FIELDS: Final[tuple[str, ...]] = (
    "name",
    "job",
    "company",
    "preferred_language",
    "location",
    "intended_use",
    "preferences",
    "projects",
)
USER_MEMORY_FILE_NAME: Final[str] = "USER.md"
CROSS_SESSION_MEMORY_FILE_NAME: Final[str] = "CROSS_SESSION.md"
SHORT_TERM_MEMORY_MARKDOWN_FILE_NAME: Final[str] = "SHORT_TERM.md"
SESSION_MEMORY_MARKDOWN_FILE_NAME: Final[str] = "LONG_TERM.md"
MEMORY_CANDIDATES_LOG_FILE_NAME: Final[str] = "MEMORY_CANDIDATES.ndjson"
VISION_EVENTS_LOG_FILE_NAME: Final[str] = "EVENTS.ndjson"
VISION_ROUTING_EVENTS_LOG_FILE_NAME: Final[str] = "ROUTING_EVENTS.ndjson"

# Backwards-compatible aliases while callers migrate away from json-named constants.
SHORT_TERM_MEMORY_JSON_FILE_NAME: Final[str] = "SHORT_TERM.json"
SESSION_MEMORY_JSON_FILE_NAME: Final[str] = "LONG_TERM.json"
USER_MEMORY_ARTIFACT_FILE_NAMES: Final[tuple[str, ...]] = (
    "user_profile.md",
    "user_profile.json",
)
SESSION_MEMORY_ARTIFACT_FILE_NAMES: Final[tuple[str, ...]] = (
    SHORT_TERM_MEMORY_MARKDOWN_FILE_NAME,
    SESSION_MEMORY_MARKDOWN_FILE_NAME,
    MEMORY_CANDIDATES_LOG_FILE_NAME,
    VISION_EVENTS_LOG_FILE_NAME,
)
EXPORTABLE_SESSION_ARTIFACT_KINDS: Final[tuple[str, ...]] = (
    "short_term_memory_markdown",
    "session_memory_markdown",
    "memory_candidate_log",
    "vision_event_log",
)

USER_MEMORY_TEMPLATE: Final[str] = """# User

## Identity

## Preferences

## Stable Facts

## Open Questions

"""

CROSS_SESSION_MEMORY_TEMPLATE: Final[str] = """# Cross-Session Memory

## Active Themes

## Ongoing Projects

## Important Recent Facts

## Follow-Up Items

"""

SHORT_TERM_MEMORY_TEMPLATE: Final[str] = """# Short-Term Memory

## Current View

## Recent Changes

## Current Task Guess

## Timestamp

"""

SESSION_MEMORY_TEMPLATE: Final[str] = """# Session Memory

## Session Goal

## What Happened

## Important Facts Learned

## Pending Follow-Ups

## Last Updated

"""

@dataclass(frozen=True, slots=True)
class UserMemoryLifecycleMetadata:
    schema_version: str = USER_MEMORY_SCHEMA_VERSION
    updated_at_ms: int | None = None
    source: str | None = None


@dataclass(frozen=True, slots=True)
class UserMemoryRecord:
    name: str | None = None
    job: str | None = None
    company: str | None = None
    preferred_language: str | None = None
    location: str | None = None
    intended_use: str | None = None
    preferences: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    metadata: UserMemoryLifecycleMetadata = field(default_factory=UserMemoryLifecycleMetadata)


@dataclass(frozen=True, slots=True)
class MemoryExportManifest:
    schema_version: str = MEMORY_EXPORT_SCHEMA_VERSION
    exported_at_ms: int | None = None
    session_retention_days: int = DEFAULT_SESSION_MEMORY_RETENTION_DAYS
    session_ids: tuple[str, ...] = ()
    included_artifact_kinds: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SessionMemoryResetEligibility:
    session_id: str
    is_active: bool
    has_persisted_memory: bool
    eligible: bool
    reason: str


@dataclass(frozen=True, slots=True)
class SessionMemoryRetentionEligibility:
    session_id: str
    status: str
    updated_at_ms: int
    cutoff_at_ms: int
    eligible: bool
    reason: str


def allowed_user_memory_fields() -> tuple[str, ...]:
    return USER_MEMORY_ALLOWLISTED_FIELDS


# Compatibility aliases while callers migrate from profile naming.
PROFILE_SCHEMA_VERSION: Final[str] = USER_MEMORY_SCHEMA_VERSION
PROFILE_METADATA_KEY: Final[str] = USER_MEMORY_METADATA_KEY
PROFILE_ALLOWLISTED_FIELDS: Final[tuple[str, ...]] = USER_MEMORY_ALLOWLISTED_FIELDS
PROFILE_ARTIFACT_FILE_NAMES: Final[tuple[str, ...]] = USER_MEMORY_ARTIFACT_FILE_NAMES
ProfileLifecycleMetadata = UserMemoryLifecycleMetadata
ProfileRecord = UserMemoryRecord


def allowed_profile_fields() -> tuple[str, ...]:
    return allowed_user_memory_fields()


__all__ = [
    "CROSS_SESSION_MEMORY_FILE_NAME",
    "CROSS_SESSION_MEMORY_TEMPLATE",
    "DEFAULT_SESSION_MEMORY_RETENTION_DAYS",
    "EXPORTABLE_SESSION_ARTIFACT_KINDS",
    "MEMORY_CANDIDATES_LOG_FILE_NAME",
    "MEMORY_EXPORT_SCHEMA_VERSION",
    "USER_MEMORY_ALLOWLISTED_FIELDS",
    "USER_MEMORY_ARTIFACT_FILE_NAMES",
    "USER_MEMORY_METADATA_KEY",
    "USER_MEMORY_SCHEMA_VERSION",
    "PROFILE_ALLOWLISTED_FIELDS",
    "PROFILE_ARTIFACT_FILE_NAMES",
    "PROFILE_METADATA_KEY",
    "PROFILE_SCHEMA_VERSION",
    "SESSION_MEMORY_TEMPLATE",
    "SESSION_MEMORY_ARTIFACT_FILE_NAMES",
    "SESSION_MEMORY_JSON_FILE_NAME",
    "SESSION_MEMORY_MARKDOWN_FILE_NAME",
    "SHORT_TERM_MEMORY_TEMPLATE",
    "SHORT_TERM_MEMORY_JSON_FILE_NAME",
    "SHORT_TERM_MEMORY_MARKDOWN_FILE_NAME",
    "USER_MEMORY_FILE_NAME",
    "USER_MEMORY_TEMPLATE",
    "VISION_EVENTS_LOG_FILE_NAME",
    "VISION_ROUTING_EVENTS_LOG_FILE_NAME",
    "MemoryExportManifest",
    "UserMemoryLifecycleMetadata",
    "UserMemoryRecord",
    "ProfileLifecycleMetadata",
    "ProfileRecord",
    "SessionMemoryResetEligibility",
    "SessionMemoryRetentionEligibility",
    "allowed_user_memory_fields",
    "allowed_profile_fields",
]
