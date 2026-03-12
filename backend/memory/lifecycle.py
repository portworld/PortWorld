from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final


PROFILE_SCHEMA_VERSION: Final[str] = "1"
MEMORY_EXPORT_SCHEMA_VERSION: Final[str] = "1"
DEFAULT_SESSION_MEMORY_RETENTION_DAYS: Final[int] = 30
PROFILE_METADATA_KEY: Final[str] = "profile_metadata"

PROFILE_ALLOWLISTED_FIELDS: Final[tuple[str, ...]] = (
    "name",
    "job",
    "company",
    "preferences",
    "projects",
)
PROFILE_ARTIFACT_FILE_NAMES: Final[tuple[str, ...]] = (
    "user_profile.md",
    "user_profile.json",
)
SHORT_TERM_MEMORY_MARKDOWN_FILE_NAME: Final[str] = "short_term_memory.md"
SHORT_TERM_MEMORY_JSON_FILE_NAME: Final[str] = "short_term_memory.json"
SESSION_MEMORY_MARKDOWN_FILE_NAME: Final[str] = "session_memory.md"
SESSION_MEMORY_JSON_FILE_NAME: Final[str] = "session_memory.json"
VISION_EVENTS_LOG_FILE_NAME: Final[str] = "vision_events.jsonl"
VISION_ROUTING_EVENTS_LOG_FILE_NAME: Final[str] = "vision_routing_events.jsonl"
SESSION_MEMORY_ARTIFACT_FILE_NAMES: Final[tuple[str, ...]] = (
    SHORT_TERM_MEMORY_MARKDOWN_FILE_NAME,
    SHORT_TERM_MEMORY_JSON_FILE_NAME,
    SESSION_MEMORY_MARKDOWN_FILE_NAME,
    SESSION_MEMORY_JSON_FILE_NAME,
    VISION_EVENTS_LOG_FILE_NAME,
    VISION_ROUTING_EVENTS_LOG_FILE_NAME,
)
EXPORTABLE_SESSION_ARTIFACT_KINDS: Final[tuple[str, ...]] = (
    "short_term_memory_markdown",
    "short_term_memory_json",
    "session_memory_markdown",
    "session_memory_json",
    "vision_event_log",
    "vision_routing_event_log",
)

PROFILE_ONBOARDING_FIELD_DESCRIPTIONS: Final[dict[str, str]] = {
    "name": "User's preferred name.",
    "job": "User's stable role or job title.",
    "company": "User's company or organization when stable and relevant.",
    "preferences": "Stable personal preferences expressed as short strings.",
    "projects": "Recurring projects, domains, or areas of work.",
}


@dataclass(frozen=True, slots=True)
class ProfileLifecycleMetadata:
    schema_version: str = PROFILE_SCHEMA_VERSION
    updated_at_ms: int | None = None
    source: str | None = None


@dataclass(frozen=True, slots=True)
class ProfileRecord:
    name: str | None = None
    job: str | None = None
    company: str | None = None
    preferences: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    metadata: ProfileLifecycleMetadata = field(default_factory=ProfileLifecycleMetadata)


@dataclass(frozen=True, slots=True)
class ProfileOnboardingPayload:
    name: str | None = None
    job: str | None = None
    company: str | None = None
    preferences: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)


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


@dataclass(frozen=True, slots=True)
class ProfilePromotionCandidate:
    source_kind: str
    source_id: str
    extracted_at_ms: int
    fields: dict[str, str | list[str]]


def allowed_profile_fields() -> tuple[str, ...]:
    return PROFILE_ALLOWLISTED_FIELDS


def profile_onboarding_field_descriptions() -> dict[str, str]:
    return dict(PROFILE_ONBOARDING_FIELD_DESCRIPTIONS)


__all__ = [
    "DEFAULT_SESSION_MEMORY_RETENTION_DAYS",
    "EXPORTABLE_SESSION_ARTIFACT_KINDS",
    "MEMORY_EXPORT_SCHEMA_VERSION",
    "PROFILE_ALLOWLISTED_FIELDS",
    "PROFILE_ARTIFACT_FILE_NAMES",
    "PROFILE_METADATA_KEY",
    "PROFILE_ONBOARDING_FIELD_DESCRIPTIONS",
    "PROFILE_SCHEMA_VERSION",
    "SESSION_MEMORY_ARTIFACT_FILE_NAMES",
    "SESSION_MEMORY_JSON_FILE_NAME",
    "SESSION_MEMORY_MARKDOWN_FILE_NAME",
    "SHORT_TERM_MEMORY_JSON_FILE_NAME",
    "SHORT_TERM_MEMORY_MARKDOWN_FILE_NAME",
    "VISION_EVENTS_LOG_FILE_NAME",
    "VISION_ROUTING_EVENTS_LOG_FILE_NAME",
    "MemoryExportManifest",
    "ProfileLifecycleMetadata",
    "ProfileOnboardingPayload",
    "ProfilePromotionCandidate",
    "ProfileRecord",
    "SessionMemoryResetEligibility",
    "SessionMemoryRetentionEligibility",
    "allowed_profile_fields",
    "profile_onboarding_field_descriptions",
]
