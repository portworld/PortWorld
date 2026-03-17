from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping


PROVIDER_KIND_REALTIME = "realtime"
PROVIDER_KIND_VISION = "vision"
PROVIDER_KIND_SEARCH = "search"

SUPPORTED_PROVIDER_KINDS: tuple[str, ...] = (
    PROVIDER_KIND_REALTIME,
    PROVIDER_KIND_VISION,
    PROVIDER_KIND_SEARCH,
)


@dataclass(frozen=True, slots=True)
class SecretBindingMetadata:
    eligible: bool
    required_env_keys: tuple[str, ...] = ()
    optional_env_keys: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderRequirementEntry:
    kind: str
    provider_id: str
    display_name: str
    summary: str
    required_env_keys: tuple[str, ...]
    optional_env_keys: tuple[str, ...] = ()
    legacy_alias_keys: tuple[str, ...] = ()
    capability_tags: tuple[str, ...] = ()
    alias_precedence_by_key: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    secret_binding: SecretBindingMetadata = field(
        default_factory=lambda: SecretBindingMetadata(eligible=True)
    )


@dataclass(frozen=True, slots=True)
class SelectedProviders:
    realtime_provider: str
    vision_enabled: bool
    vision_provider: str | None
    search_enabled: bool
    search_provider: str | None


@dataclass(frozen=True, slots=True)
class SelectedProviderKeySet:
    entries: tuple[ProviderRequirementEntry, ...]
    required_env_keys: tuple[str, ...]
    optional_env_keys: tuple[str, ...]
    legacy_alias_keys: tuple[str, ...]
    secret_binding_required_env_keys: tuple[str, ...]
    secret_binding_optional_env_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MissingSecretDiagnostics:
    selected: SelectedProviders
    required_env_keys: tuple[str, ...]
    optional_env_keys: tuple[str, ...]
    missing_required_env_keys: tuple[str, ...]
    key_presence: Mapping[str, bool]
    resolved_values: Mapping[str, str | None]
    resolved_sources: Mapping[str, str | None]

    def to_payload(self) -> dict[str, object]:
        return {
            "selected": {
                "realtime_provider": self.selected.realtime_provider,
                "vision_enabled": self.selected.vision_enabled,
                "vision_provider": self.selected.vision_provider,
                "search_enabled": self.selected.search_enabled,
                "search_provider": self.selected.search_provider,
            },
            "required_env_keys": list(self.required_env_keys),
            "optional_env_keys": list(self.optional_env_keys),
            "missing_required_env_keys": list(self.missing_required_env_keys),
            "key_presence": dict(self.key_presence),
            "resolved_values": dict(self.resolved_values),
            "resolved_sources": dict(self.resolved_sources),
        }


PROVIDER_REQUIREMENTS: tuple[ProviderRequirementEntry, ...] = (
    ProviderRequirementEntry(
        kind=PROVIDER_KIND_REALTIME,
        provider_id="openai",
        display_name="OpenAI Realtime",
        summary="Realtime session provider backed by OpenAI.",
        required_env_keys=("OPENAI_API_KEY",),
        optional_env_keys=(
            "OPENAI_REALTIME_MODEL",
            "OPENAI_REALTIME_VOICE",
            "OPENAI_REALTIME_INSTRUCTIONS",
        ),
        capability_tags=("realtime_sessions", "audio_streaming", "tool_calling", "voice_selection"),
        secret_binding=SecretBindingMetadata(
            eligible=True,
            required_env_keys=("OPENAI_API_KEY",),
        ),
    ),
    ProviderRequirementEntry(
        kind=PROVIDER_KIND_REALTIME,
        provider_id="gemini_live",
        display_name="Gemini Live",
        summary="Realtime session provider backed by Google Gemini Live.",
        required_env_keys=("GEMINI_LIVE_API_KEY",),
        optional_env_keys=(
            "GEMINI_LIVE_MODEL",
            "GEMINI_LIVE_BASE_URL",
            "GEMINI_LIVE_ENDPOINT",
        ),
        capability_tags=("realtime_sessions", "audio_streaming", "tool_calling"),
        secret_binding=SecretBindingMetadata(
            eligible=True,
            required_env_keys=("GEMINI_LIVE_API_KEY",),
        ),
    ),
    ProviderRequirementEntry(
        kind=PROVIDER_KIND_VISION,
        provider_id="mistral",
        display_name="Mistral",
        summary="Vision-memory provider using the native Mistral adapter.",
        required_env_keys=("VISION_MISTRAL_API_KEY",),
        optional_env_keys=("VISION_MISTRAL_BASE_URL",),
        legacy_alias_keys=(
            "VISION_PROVIDER_API_KEY",
            "VISION_PROVIDER_BASE_URL",
            "MISTRAL_API_KEY",
            "MISTRAL_BASE_URL",
        ),
        capability_tags=("vision_memory", "structured_output_fallback"),
        alias_precedence_by_key=MappingProxyType(
            {
                "VISION_MISTRAL_API_KEY": (
                    "VISION_PROVIDER_API_KEY",
                    "MISTRAL_API_KEY",
                ),
                "VISION_MISTRAL_BASE_URL": (
                    "VISION_PROVIDER_BASE_URL",
                    "MISTRAL_BASE_URL",
                ),
            }
        ),
        secret_binding=SecretBindingMetadata(
            eligible=True,
            required_env_keys=("VISION_MISTRAL_API_KEY",),
        ),
    ),
    ProviderRequirementEntry(
        kind=PROVIDER_KIND_VISION,
        provider_id="openai",
        display_name="OpenAI Vision",
        summary="Vision-memory provider using OpenAI responses.",
        required_env_keys=("VISION_OPENAI_API_KEY",),
        optional_env_keys=("VISION_OPENAI_BASE_URL",),
        legacy_alias_keys=("VISION_PROVIDER_API_KEY", "VISION_PROVIDER_BASE_URL"),
        capability_tags=("vision_memory", "structured_output"),
        alias_precedence_by_key=MappingProxyType(
            {
                "VISION_OPENAI_API_KEY": ("VISION_PROVIDER_API_KEY",),
                "VISION_OPENAI_BASE_URL": ("VISION_PROVIDER_BASE_URL",),
            }
        ),
        secret_binding=SecretBindingMetadata(
            eligible=True,
            required_env_keys=("VISION_OPENAI_API_KEY",),
        ),
    ),
    ProviderRequirementEntry(
        kind=PROVIDER_KIND_VISION,
        provider_id="azure_openai",
        display_name="Azure OpenAI Vision",
        summary="Vision-memory provider backed by Azure OpenAI deployments.",
        required_env_keys=("VISION_AZURE_OPENAI_API_KEY",),
        optional_env_keys=(
            "VISION_AZURE_OPENAI_ENDPOINT",
            "VISION_AZURE_OPENAI_API_VERSION",
            "VISION_AZURE_OPENAI_DEPLOYMENT",
        ),
        legacy_alias_keys=("VISION_PROVIDER_API_KEY",),
        capability_tags=("vision_memory", "structured_output"),
        alias_precedence_by_key=MappingProxyType(
            {
                "VISION_AZURE_OPENAI_API_KEY": ("VISION_PROVIDER_API_KEY",),
            }
        ),
        secret_binding=SecretBindingMetadata(
            eligible=True,
            required_env_keys=("VISION_AZURE_OPENAI_API_KEY",),
        ),
    ),
    ProviderRequirementEntry(
        kind=PROVIDER_KIND_VISION,
        provider_id="gemini",
        display_name="Gemini Vision",
        summary="Vision-memory provider using the Gemini multimodal API.",
        required_env_keys=("VISION_GEMINI_API_KEY",),
        optional_env_keys=("VISION_GEMINI_BASE_URL",),
        legacy_alias_keys=("VISION_PROVIDER_API_KEY", "VISION_PROVIDER_BASE_URL"),
        capability_tags=("vision_memory", "structured_output"),
        alias_precedence_by_key=MappingProxyType(
            {
                "VISION_GEMINI_API_KEY": ("VISION_PROVIDER_API_KEY",),
                "VISION_GEMINI_BASE_URL": ("VISION_PROVIDER_BASE_URL",),
            }
        ),
        secret_binding=SecretBindingMetadata(
            eligible=True,
            required_env_keys=("VISION_GEMINI_API_KEY",),
        ),
    ),
    ProviderRequirementEntry(
        kind=PROVIDER_KIND_VISION,
        provider_id="claude",
        display_name="Claude Vision",
        summary="Vision-memory provider using Anthropic Claude messages.",
        required_env_keys=("VISION_CLAUDE_API_KEY",),
        optional_env_keys=("VISION_CLAUDE_BASE_URL",),
        legacy_alias_keys=("VISION_PROVIDER_API_KEY", "VISION_PROVIDER_BASE_URL"),
        capability_tags=("vision_memory"),
        alias_precedence_by_key=MappingProxyType(
            {
                "VISION_CLAUDE_API_KEY": ("VISION_PROVIDER_API_KEY",),
                "VISION_CLAUDE_BASE_URL": ("VISION_PROVIDER_BASE_URL",),
            }
        ),
        secret_binding=SecretBindingMetadata(
            eligible=True,
            required_env_keys=("VISION_CLAUDE_API_KEY",),
        ),
    ),
    ProviderRequirementEntry(
        kind=PROVIDER_KIND_VISION,
        provider_id="bedrock",
        display_name="Bedrock Vision",
        summary="Vision-memory provider using AWS Bedrock runtime credentials.",
        required_env_keys=(),
        optional_env_keys=(
            "VISION_BEDROCK_REGION",
            "VISION_BEDROCK_AWS_ACCESS_KEY_ID",
            "VISION_BEDROCK_AWS_SECRET_ACCESS_KEY",
            "VISION_BEDROCK_AWS_SESSION_TOKEN",
        ),
        legacy_alias_keys=(
            "AWS_REGION",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
        ),
        capability_tags=("vision_memory", "aws_sdk"),
        alias_precedence_by_key=MappingProxyType(
            {
                "VISION_BEDROCK_REGION": ("AWS_REGION",),
                "VISION_BEDROCK_AWS_ACCESS_KEY_ID": ("AWS_ACCESS_KEY_ID",),
                "VISION_BEDROCK_AWS_SECRET_ACCESS_KEY": ("AWS_SECRET_ACCESS_KEY",),
                "VISION_BEDROCK_AWS_SESSION_TOKEN": ("AWS_SESSION_TOKEN",),
            }
        ),
        secret_binding=SecretBindingMetadata(
            eligible=True,
            optional_env_keys=(
                "VISION_BEDROCK_AWS_ACCESS_KEY_ID",
                "VISION_BEDROCK_AWS_SECRET_ACCESS_KEY",
                "VISION_BEDROCK_AWS_SESSION_TOKEN",
            ),
        ),
    ),
    ProviderRequirementEntry(
        kind=PROVIDER_KIND_VISION,
        provider_id="groq",
        display_name="Groq Vision",
        summary="Vision-memory provider using Groq multimodal inference.",
        required_env_keys=("VISION_GROQ_API_KEY",),
        optional_env_keys=("VISION_GROQ_BASE_URL",),
        legacy_alias_keys=("VISION_PROVIDER_API_KEY", "VISION_PROVIDER_BASE_URL"),
        capability_tags=("vision_memory", "structured_output"),
        alias_precedence_by_key=MappingProxyType(
            {
                "VISION_GROQ_API_KEY": ("VISION_PROVIDER_API_KEY",),
                "VISION_GROQ_BASE_URL": ("VISION_PROVIDER_BASE_URL",),
            }
        ),
        secret_binding=SecretBindingMetadata(
            eligible=True,
            required_env_keys=("VISION_GROQ_API_KEY",),
        ),
    ),
    ProviderRequirementEntry(
        kind=PROVIDER_KIND_SEARCH,
        provider_id="tavily",
        display_name="Tavily Search",
        summary="Web-search provider used by realtime tooling.",
        required_env_keys=("TAVILY_API_KEY",),
        optional_env_keys=("TAVILY_BASE_URL",),
        capability_tags=("web_search",),
        secret_binding=SecretBindingMetadata(
            eligible=True,
            required_env_keys=("TAVILY_API_KEY",),
        ),
    ),
)


_REQUIREMENTS_INDEX: dict[tuple[str, str], ProviderRequirementEntry] = {
    (entry.kind, entry.provider_id): entry for entry in PROVIDER_REQUIREMENTS
}


_SETTINGS_ATTR_BY_ENV_KEY: Mapping[str, str] = MappingProxyType(
    {
        "OPENAI_API_KEY": "openai_api_key",
        "GEMINI_LIVE_API_KEY": "gemini_live_api_key",
        "VISION_MISTRAL_API_KEY": "vision_mistral_api_key",
        "VISION_MISTRAL_BASE_URL": "vision_mistral_base_url",
        "VISION_OPENAI_API_KEY": "vision_openai_api_key",
        "VISION_OPENAI_BASE_URL": "vision_openai_base_url",
        "VISION_AZURE_OPENAI_API_KEY": "vision_azure_openai_api_key",
        "VISION_AZURE_OPENAI_ENDPOINT": "vision_azure_openai_endpoint",
        "VISION_AZURE_OPENAI_API_VERSION": "vision_azure_openai_api_version",
        "VISION_AZURE_OPENAI_DEPLOYMENT": "vision_azure_openai_deployment",
        "VISION_GEMINI_API_KEY": "vision_gemini_api_key",
        "VISION_GEMINI_BASE_URL": "vision_gemini_base_url",
        "VISION_CLAUDE_API_KEY": "vision_claude_api_key",
        "VISION_CLAUDE_BASE_URL": "vision_claude_base_url",
        "VISION_BEDROCK_REGION": "vision_bedrock_region",
        "VISION_BEDROCK_AWS_ACCESS_KEY_ID": "vision_bedrock_aws_access_key_id",
        "VISION_BEDROCK_AWS_SECRET_ACCESS_KEY": "vision_bedrock_aws_secret_access_key",
        "VISION_BEDROCK_AWS_SESSION_TOKEN": "vision_bedrock_aws_session_token",
        "VISION_GROQ_API_KEY": "vision_groq_api_key",
        "VISION_GROQ_BASE_URL": "vision_groq_base_url",
        "VISION_PROVIDER_API_KEY": "vision_provider_api_key",
        "VISION_PROVIDER_BASE_URL": "vision_provider_base_url",
        "MISTRAL_API_KEY": "mistral_api_key",
        "MISTRAL_BASE_URL": "mistral_base_url",
        "TAVILY_API_KEY": "tavily_api_key",
        "TAVILY_BASE_URL": "tavily_base_url",
        "REALTIME_PROVIDER": "realtime_provider",
        "VISION_MEMORY_ENABLED": "vision_memory_enabled",
        "VISION_MEMORY_PROVIDER": "vision_memory_provider",
        "REALTIME_TOOLING_ENABLED": "realtime_tooling_enabled",
        "REALTIME_WEB_SEARCH_PROVIDER": "realtime_web_search_provider",
    }
)


def list_provider_requirements(*, kind: str | None = None) -> tuple[ProviderRequirementEntry, ...]:
    if kind is None:
        return PROVIDER_REQUIREMENTS
    return tuple(entry for entry in PROVIDER_REQUIREMENTS if entry.kind == kind)


def supported_provider_ids(kind: str) -> tuple[str, ...]:
    return tuple(entry.provider_id for entry in list_provider_requirements(kind=kind))


def get_provider_requirement(*, kind: str, provider_id: str) -> ProviderRequirementEntry:
    key = (kind.strip().lower(), provider_id.strip().lower())
    try:
        return _REQUIREMENTS_INDEX[key]
    except KeyError as exc:
        supported = ", ".join(supported_provider_ids(key[0]))
        raise ValueError(
            f"Unsupported {key[0]} provider {provider_id!r}. Supported values: {supported}"
        ) from exc


def resolve_selected_providers(source: Mapping[str, Any] | object) -> SelectedProviders:
    realtime_provider = _normalized_provider(
        _source_value(source, "REALTIME_PROVIDER", fallback_attr="realtime_provider"),
        default="openai",
    )
    vision_enabled = _parse_bool(
        _source_value(source, "VISION_MEMORY_ENABLED", fallback_attr="vision_memory_enabled"),
        default=False,
    )
    vision_provider = None
    if vision_enabled:
        vision_provider = _normalized_provider(
            _source_value(source, "VISION_MEMORY_PROVIDER", fallback_attr="vision_memory_provider"),
            default="mistral",
        )

    search_enabled = _parse_bool(
        _source_value(source, "REALTIME_TOOLING_ENABLED", fallback_attr="realtime_tooling_enabled"),
        default=False,
    )
    search_provider = None
    if search_enabled:
        search_provider = _normalized_provider(
            _source_value(
                source,
                "REALTIME_WEB_SEARCH_PROVIDER",
                fallback_attr="realtime_web_search_provider",
            ),
            default="tavily",
        )

    return SelectedProviders(
        realtime_provider=realtime_provider,
        vision_enabled=vision_enabled,
        vision_provider=vision_provider,
        search_enabled=search_enabled,
        search_provider=search_provider,
    )


def compute_selected_provider_key_set(selected: SelectedProviders) -> SelectedProviderKeySet:
    entries: list[ProviderRequirementEntry] = [
        get_provider_requirement(kind=PROVIDER_KIND_REALTIME, provider_id=selected.realtime_provider)
    ]
    if selected.vision_enabled and selected.vision_provider is not None:
        entries.append(
            get_provider_requirement(kind=PROVIDER_KIND_VISION, provider_id=selected.vision_provider)
        )
    if selected.search_enabled and selected.search_provider is not None:
        entries.append(
            get_provider_requirement(kind=PROVIDER_KIND_SEARCH, provider_id=selected.search_provider)
        )

    required: list[str] = []
    optional: list[str] = []
    legacy: list[str] = []
    secret_binding_required: list[str] = []
    secret_binding_optional: list[str] = []

    for entry in entries:
        _append_unique(required, entry.required_env_keys)
        _append_unique(optional, entry.optional_env_keys)
        _append_unique(legacy, entry.legacy_alias_keys)
        if entry.secret_binding.eligible:
            _append_unique(secret_binding_required, entry.secret_binding.required_env_keys)
            _append_unique(secret_binding_optional, entry.secret_binding.optional_env_keys)

    return SelectedProviderKeySet(
        entries=tuple(entries),
        required_env_keys=tuple(required),
        optional_env_keys=tuple(optional),
        legacy_alias_keys=tuple(legacy),
        secret_binding_required_env_keys=tuple(secret_binding_required),
        secret_binding_optional_env_keys=tuple(secret_binding_optional),
    )


def resolve_effective_env_value(
    *,
    values: Mapping[str, Any] | object,
    provider_kind: str,
    provider_id: str,
    env_key: str,
) -> tuple[str | None, str | None]:
    requirement = get_provider_requirement(kind=provider_kind, provider_id=provider_id)
    return _resolve_effective_env_value_for_entry(values=values, entry=requirement, env_key=env_key)


def build_missing_secret_diagnostics(
    source: Mapping[str, Any] | object,
    *,
    selected: SelectedProviders | None = None,
) -> MissingSecretDiagnostics:
    selected_providers = selected or resolve_selected_providers(source)
    key_set = compute_selected_provider_key_set(selected_providers)

    required_presence: dict[str, bool] = {}
    resolved_values: dict[str, str | None] = {}
    resolved_sources: dict[str, str | None] = {}
    missing_required: list[str] = []

    for entry in key_set.entries:
        for env_key in entry.required_env_keys:
            if env_key in required_presence:
                continue
            value, source_key = _resolve_effective_env_value_for_entry(
                values=source,
                entry=entry,
                env_key=env_key,
            )
            resolved_values[env_key] = value
            resolved_sources[env_key] = source_key
            is_present = bool((value or "").strip())
            required_presence[env_key] = is_present
            if not is_present:
                missing_required.append(env_key)

    for entry in key_set.entries:
        for env_key in entry.optional_env_keys:
            if env_key in resolved_values:
                continue
            value, source_key = _resolve_effective_env_value_for_entry(
                values=source,
                entry=entry,
                env_key=env_key,
            )
            resolved_values[env_key] = value
            resolved_sources[env_key] = source_key

    return MissingSecretDiagnostics(
        selected=selected_providers,
        required_env_keys=key_set.required_env_keys,
        optional_env_keys=key_set.optional_env_keys,
        missing_required_env_keys=tuple(missing_required),
        key_presence=MappingProxyType(required_presence),
        resolved_values=MappingProxyType(resolved_values),
        resolved_sources=MappingProxyType(resolved_sources),
    )


def _resolve_effective_env_value_for_entry(
    *,
    values: Mapping[str, Any] | object,
    entry: ProviderRequirementEntry,
    env_key: str,
) -> tuple[str | None, str | None]:
    for key in (env_key, *entry.alias_precedence_by_key.get(env_key, ())):
        raw_value = _source_value(values, key)
        if raw_value is None:
            continue
        text = str(raw_value).strip()
        if text:
            return text, key
    return None, None


def _source_value(
    source: Mapping[str, Any] | object,
    env_key: str,
    *,
    fallback_attr: str | None = None,
) -> Any:
    if isinstance(source, Mapping):
        return source.get(env_key)

    attr_name = fallback_attr or _SETTINGS_ATTR_BY_ENV_KEY.get(env_key)
    if attr_name is None:
        return None
    return getattr(source, attr_name, None)


def _normalized_provider(value: Any, *, default: str) -> str:
    text = "" if value is None else str(value).strip().lower()
    return text or default


def _parse_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _append_unique(target: list[str], values: tuple[str, ...]) -> None:
    for value in values:
        if value not in target:
            target.append(value)
