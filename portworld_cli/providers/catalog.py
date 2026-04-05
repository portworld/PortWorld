from __future__ import annotations

from dataclasses import dataclass


PROVIDER_KIND_REALTIME = "realtime"
PROVIDER_KIND_VISION = "vision"
PROVIDER_KIND_SEARCH = "search"
PROVIDER_KIND_CLOUD = "cloud"

SUPPORTED_PROVIDER_KINDS: tuple[str, ...] = (
    PROVIDER_KIND_REALTIME,
    PROVIDER_KIND_VISION,
    PROVIDER_KIND_SEARCH,
)


@dataclass(frozen=True, slots=True)
class ProviderCatalogEntry:
    id: str
    display_name: str
    kind: str
    summary: str
    default: bool
    aliases: tuple[str, ...] = ()
    capability_tags: tuple[str, ...] = ()
    supported_targets: tuple[str, ...] = ()
    required_clis: tuple[str, ...] = ()
    required_env_keys: tuple[str, ...] = ()
    optional_env_keys: tuple[str, ...] = ()
    setup_notes: tuple[str, ...] = ()
    command_paths: tuple[str, ...] = ()

    def matches(self, provider_id: str) -> bool:
        normalized = provider_id.strip().lower()
        return normalized == self.id or normalized in self.aliases

    def to_summary_payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "kind": self.kind,
            "aliases": list(self.aliases),
            "default": self.default,
            "summary": self.summary,
            "capability_tags": list(self.capability_tags),
        }

    def to_detail_payload(self) -> dict[str, object]:
        payload = self.to_summary_payload()
        payload.update(
            {
                "supported_targets": list(self.supported_targets),
                "required_clis": list(self.required_clis),
                "required_env_keys": list(self.required_env_keys),
                "optional_env_keys": list(self.optional_env_keys),
                "setup_notes": list(self.setup_notes),
                "command_paths": list(self.command_paths),
            }
        )
        return payload


@dataclass(frozen=True, slots=True)
class RuntimeProviderMetadata:
    provider_id: str
    display_name: str
    kind: str
    summary: str
    required_env_keys: tuple[str, ...]
    optional_env_keys: tuple[str, ...]
    required_secret_env_keys: tuple[str, ...] = ()
    optional_secret_env_keys: tuple[str, ...] = ()
    required_non_secret_env_keys: tuple[str, ...] = ()
    optional_non_secret_env_keys: tuple[str, ...] = ()
    capability_tags: tuple[str, ...] = ()


_DEFAULT_PROVIDER_IDS: dict[str, str] = {
    PROVIDER_KIND_REALTIME: "openai",
    PROVIDER_KIND_VISION: "mistral",
    PROVIDER_KIND_SEARCH: "tavily",
}
_CLOUD_PROVIDER_CAPABILITY_TAGS: tuple[str, ...] = ("deploy", "status", "logs", "update_deploy")

_RUNTIME_PROVIDER_METADATA: tuple[RuntimeProviderMetadata, ...] = (
    RuntimeProviderMetadata(
        provider_id="openai",
        display_name="OpenAI Realtime",
        kind=PROVIDER_KIND_REALTIME,
        summary="Realtime session provider backed by OpenAI.",
        required_env_keys=("OPENAI_API_KEY",),
        optional_env_keys=(
            "REALTIME_MODEL",
            "REALTIME_VOICE",
            "REALTIME_INSTRUCTIONS",
            "REALTIME_INCLUDE_TURN_DETECTION",
            "REALTIME_ENABLE_MANUAL_TURN_FALLBACK",
            "REALTIME_MANUAL_TURN_FALLBACK_DELAY_MS",
        ),
        required_secret_env_keys=("OPENAI_API_KEY",),
        optional_non_secret_env_keys=(
            "REALTIME_MODEL",
            "REALTIME_VOICE",
            "REALTIME_INSTRUCTIONS",
            "REALTIME_INCLUDE_TURN_DETECTION",
            "REALTIME_ENABLE_MANUAL_TURN_FALLBACK",
            "REALTIME_MANUAL_TURN_FALLBACK_DELAY_MS",
        ),
        capability_tags=("realtime_sessions", "audio_streaming", "tool_calling", "voice_selection"),
    ),
    RuntimeProviderMetadata(
        provider_id="gemini_live",
        display_name="Gemini Live",
        kind=PROVIDER_KIND_REALTIME,
        summary="Realtime session provider backed by Google Gemini Live.",
        required_env_keys=("GEMINI_LIVE_API_KEY",),
        optional_env_keys=(
            "GEMINI_LIVE_MODEL",
            "GEMINI_LIVE_BASE_URL",
            "GEMINI_LIVE_ENDPOINT",
            "REALTIME_INSTRUCTIONS",
            "REALTIME_MANUAL_TURN_FALLBACK_DELAY_MS",
        ),
        required_secret_env_keys=("GEMINI_LIVE_API_KEY",),
        optional_non_secret_env_keys=(
            "GEMINI_LIVE_MODEL",
            "GEMINI_LIVE_BASE_URL",
            "GEMINI_LIVE_ENDPOINT",
            "REALTIME_INSTRUCTIONS",
            "REALTIME_MANUAL_TURN_FALLBACK_DELAY_MS",
        ),
        capability_tags=("realtime_sessions", "audio_streaming", "tool_calling"),
    ),
    RuntimeProviderMetadata(
        provider_id="mistral",
        display_name="Mistral",
        kind=PROVIDER_KIND_VISION,
        summary="Vision-memory provider using the native Mistral adapter.",
        required_env_keys=("VISION_MISTRAL_API_KEY",),
        optional_env_keys=("VISION_MISTRAL_MODEL", "VISION_MISTRAL_BASE_URL"),
        required_secret_env_keys=("VISION_MISTRAL_API_KEY",),
        optional_non_secret_env_keys=("VISION_MISTRAL_MODEL", "VISION_MISTRAL_BASE_URL"),
        capability_tags=("vision_memory", "structured_output_fallback"),
    ),
    RuntimeProviderMetadata(
        provider_id="nvidia_integrate",
        display_name="NVIDIA Integrate Vision",
        kind=PROVIDER_KIND_VISION,
        summary="Vision-memory provider backed by NVIDIA Integrate/NIM OpenAI-compatible chat completions.",
        required_env_keys=("VISION_NVIDIA_API_KEY",),
        optional_env_keys=("VISION_NVIDIA_MODEL", "VISION_NVIDIA_BASE_URL"),
        required_secret_env_keys=("VISION_NVIDIA_API_KEY",),
        optional_non_secret_env_keys=("VISION_NVIDIA_MODEL", "VISION_NVIDIA_BASE_URL"),
        capability_tags=("vision_memory", "structured_output_fallback"),
    ),
    RuntimeProviderMetadata(
        provider_id="openai",
        display_name="OpenAI Vision",
        kind=PROVIDER_KIND_VISION,
        summary="Vision-memory provider using OpenAI responses.",
        required_env_keys=("VISION_OPENAI_API_KEY",),
        optional_env_keys=("VISION_OPENAI_MODEL", "VISION_OPENAI_BASE_URL"),
        required_secret_env_keys=("VISION_OPENAI_API_KEY",),
        optional_non_secret_env_keys=("VISION_OPENAI_MODEL", "VISION_OPENAI_BASE_URL"),
        capability_tags=("vision_memory", "structured_output"),
    ),
    RuntimeProviderMetadata(
        provider_id="azure_openai",
        display_name="Azure OpenAI Vision",
        kind=PROVIDER_KIND_VISION,
        summary="Vision-memory provider backed by Azure OpenAI deployments.",
        required_env_keys=("VISION_AZURE_OPENAI_API_KEY",),
        optional_env_keys=(
            "VISION_AZURE_OPENAI_MODEL",
            "VISION_AZURE_OPENAI_API_VERSION",
            "VISION_AZURE_OPENAI_DEPLOYMENT",
        ),
        required_secret_env_keys=("VISION_AZURE_OPENAI_API_KEY",),
        required_non_secret_env_keys=("VISION_AZURE_OPENAI_ENDPOINT",),
        optional_non_secret_env_keys=(
            "VISION_AZURE_OPENAI_MODEL",
            "VISION_AZURE_OPENAI_API_VERSION",
            "VISION_AZURE_OPENAI_DEPLOYMENT",
        ),
        capability_tags=("vision_memory", "structured_output"),
    ),
    RuntimeProviderMetadata(
        provider_id="gemini",
        display_name="Gemini Vision",
        kind=PROVIDER_KIND_VISION,
        summary="Vision-memory provider using the Gemini multimodal API.",
        required_env_keys=("VISION_GEMINI_API_KEY",),
        optional_env_keys=("VISION_GEMINI_MODEL", "VISION_GEMINI_BASE_URL"),
        required_secret_env_keys=("VISION_GEMINI_API_KEY",),
        optional_non_secret_env_keys=("VISION_GEMINI_MODEL", "VISION_GEMINI_BASE_URL"),
        capability_tags=("vision_memory", "structured_output"),
    ),
    RuntimeProviderMetadata(
        provider_id="claude",
        display_name="Claude Vision",
        kind=PROVIDER_KIND_VISION,
        summary="Vision-memory provider using Anthropic Claude messages.",
        required_env_keys=("VISION_CLAUDE_API_KEY",),
        optional_env_keys=("VISION_CLAUDE_MODEL", "VISION_CLAUDE_BASE_URL"),
        required_secret_env_keys=("VISION_CLAUDE_API_KEY",),
        optional_non_secret_env_keys=("VISION_CLAUDE_MODEL", "VISION_CLAUDE_BASE_URL"),
        capability_tags=("vision_memory",),
    ),
    RuntimeProviderMetadata(
        provider_id="bedrock",
        display_name="Bedrock Vision",
        kind=PROVIDER_KIND_VISION,
        summary="Vision-memory provider using AWS Bedrock runtime credentials.",
        required_env_keys=(),
        optional_env_keys=(
            "VISION_BEDROCK_MODEL",
            "VISION_BEDROCK_AWS_ACCESS_KEY_ID",
            "VISION_BEDROCK_AWS_SECRET_ACCESS_KEY",
            "VISION_BEDROCK_AWS_SESSION_TOKEN",
        ),
        optional_secret_env_keys=(
            "VISION_BEDROCK_AWS_ACCESS_KEY_ID",
            "VISION_BEDROCK_AWS_SECRET_ACCESS_KEY",
            "VISION_BEDROCK_AWS_SESSION_TOKEN",
        ),
        required_non_secret_env_keys=("VISION_BEDROCK_REGION",),
        optional_non_secret_env_keys=("VISION_BEDROCK_MODEL",),
        capability_tags=("vision_memory", "aws_sdk"),
    ),
    RuntimeProviderMetadata(
        provider_id="groq",
        display_name="Groq Vision",
        kind=PROVIDER_KIND_VISION,
        summary="Vision-memory provider using Groq multimodal inference.",
        required_env_keys=("VISION_GROQ_API_KEY",),
        optional_env_keys=("VISION_GROQ_MODEL", "VISION_GROQ_BASE_URL"),
        required_secret_env_keys=("VISION_GROQ_API_KEY",),
        optional_non_secret_env_keys=("VISION_GROQ_MODEL", "VISION_GROQ_BASE_URL"),
        capability_tags=("vision_memory", "structured_output"),
    ),
    RuntimeProviderMetadata(
        provider_id="tavily",
        display_name="Tavily Search",
        kind=PROVIDER_KIND_SEARCH,
        summary="Web-search provider used by realtime tooling.",
        required_env_keys=("TAVILY_API_KEY",),
        optional_env_keys=("TAVILY_BASE_URL",),
        required_secret_env_keys=("TAVILY_API_KEY",),
        optional_non_secret_env_keys=("TAVILY_BASE_URL",),
        capability_tags=("web_search",),
    ),
)


def _merge_key_sets(*key_sets: tuple[str, ...]) -> tuple[str, ...]:
    ordered: list[str] = []
    for key_set in key_sets:
        for key in key_set:
            if key not in ordered:
                ordered.append(key)
    return tuple(ordered)


def _runtime_catalog_entries() -> tuple[ProviderCatalogEntry, ...]:
    entries: list[ProviderCatalogEntry] = []
    for metadata in _RUNTIME_PROVIDER_METADATA:
        setup_notes: list[str] = []
        if metadata.required_secret_env_keys:
            setup_notes.append("Required secrets: " + ", ".join(metadata.required_secret_env_keys))
        if metadata.required_non_secret_env_keys:
            setup_notes.append("Required config: " + ", ".join(metadata.required_non_secret_env_keys))
        if metadata.optional_secret_env_keys:
            setup_notes.append("Optional secrets: " + ", ".join(metadata.optional_secret_env_keys))
        if metadata.optional_non_secret_env_keys:
            setup_notes.append("Optional config: " + ", ".join(metadata.optional_non_secret_env_keys))
        setup_notes.append("Configure with `portworld init` or `portworld config edit providers`.")
        entries.append(
            ProviderCatalogEntry(
                id=metadata.provider_id,
                display_name=metadata.display_name,
                kind=metadata.kind,
                summary=metadata.summary,
                default=_DEFAULT_PROVIDER_IDS.get(metadata.kind) == metadata.provider_id,
                capability_tags=metadata.capability_tags,
                required_env_keys=_merge_key_sets(
                    metadata.required_env_keys,
                    metadata.required_non_secret_env_keys,
                ),
                optional_env_keys=_merge_key_sets(
                    metadata.optional_env_keys,
                    metadata.optional_secret_env_keys,
                    metadata.optional_non_secret_env_keys,
                ),
                setup_notes=tuple(setup_notes),
                command_paths=(
                    "portworld init",
                    "portworld config edit providers",
                    "portworld config show",
                    "portworld doctor --target local",
                    "portworld doctor --target gcp-cloud-run",
                    "portworld doctor --target aws-ecs-fargate",
                    "portworld doctor --target azure-container-apps",
                ),
            )
        )
    return tuple(entries)


def _cloud_provider_entry(
    *,
    provider_id: str,
    display_name: str,
    summary: str,
    alias: str,
    required_cli: str,
    setup_notes: tuple[str, ...],
    command_paths: tuple[str, ...],
) -> ProviderCatalogEntry:
    return ProviderCatalogEntry(
        id=provider_id,
        display_name=display_name,
        kind=PROVIDER_KIND_CLOUD,
        summary=summary,
        default=(provider_id == "gcp"),
        aliases=(alias,),
        capability_tags=_CLOUD_PROVIDER_CAPABILITY_TAGS,
        supported_targets=(alias,),
        required_clis=(required_cli,),
        setup_notes=setup_notes,
        command_paths=command_paths,
    )


PROVIDER_CATALOG: tuple[ProviderCatalogEntry, ...] = (
    _cloud_provider_entry(
        provider_id="gcp",
        display_name="GCP Cloud Run",
        summary="Managed deployment path for PortWorld on Google Cloud Run.",
        alias="gcp-cloud-run",
        required_cli="gcloud",
        setup_notes=(
            "Authenticate with `gcloud auth login` before managed commands.",
            "Set or pass the active project and Cloud Run region.",
            "Use `portworld doctor --target gcp-cloud-run` before first deploy.",
        ),
        command_paths=(
            "portworld doctor --target gcp-cloud-run",
            "portworld deploy gcp-cloud-run",
            "portworld status",
            "portworld logs gcp-cloud-run",
            "portworld update deploy",
        ),
    ),
    _cloud_provider_entry(
        provider_id="aws",
        display_name="AWS ECS/Fargate",
        summary="Managed deployment path for PortWorld on AWS ECS/Fargate with CloudFront, ALB, S3 memory storage, and Postgres operational metadata.",
        alias="aws-ecs-fargate",
        required_cli="aws",
        setup_notes=(
            "Configure AWS credentials with `aws configure` before managed commands.",
            "Default deploy provisions ECS/Fargate, CloudFront, ALB, ECR, S3, and RDS for a one-click managed path.",
            "Use `portworld doctor --target aws-ecs-fargate` before first deploy.",
        ),
        command_paths=(
            "portworld doctor --target aws-ecs-fargate",
            "portworld deploy aws-ecs-fargate",
            "portworld logs aws-ecs-fargate",
            "portworld status",
            "portworld update deploy",
        ),
    ),
    _cloud_provider_entry(
        provider_id="azure",
        display_name="Azure Container Apps",
        summary="Managed deployment path for PortWorld on Azure Container Apps with Blob memory storage and Postgres operational metadata.",
        alias="azure-container-apps",
        required_cli="az",
        setup_notes=(
            "Authenticate with `az login` before managed commands.",
            "Default deploy provisions Container Apps, ACR, Blob storage, and PostgreSQL for a one-click managed path.",
            "Use `portworld doctor --target azure-container-apps` before first deploy.",
        ),
        command_paths=(
            "portworld doctor --target azure-container-apps",
            "portworld deploy azure-container-apps",
            "portworld logs azure-container-apps",
            "portworld status",
            "portworld update deploy",
        ),
    ),
    *_runtime_catalog_entries(),
)


def list_providers() -> tuple[ProviderCatalogEntry, ...]:
    return PROVIDER_CATALOG


def resolve_provider(provider_id: str) -> ProviderCatalogEntry | None:
    normalized = provider_id.strip().lower()
    for entry in PROVIDER_CATALOG:
        if entry.matches(normalized):
            return entry
    return None


def supported_provider_ids() -> tuple[str, ...]:
    values: list[str] = []
    for entry in PROVIDER_CATALOG:
        if entry.id not in values:
            values.append(entry.id)
        for alias in entry.aliases:
            if alias not in values:
                values.append(alias)
    return tuple(values)


def supported_runtime_provider_ids(kind: str) -> tuple[str, ...]:
    if kind not in SUPPORTED_PROVIDER_KINDS:
        raise ValueError(f"Unsupported provider kind: {kind}")
    return tuple(
        metadata.provider_id
        for metadata in _RUNTIME_PROVIDER_METADATA
        if metadata.kind == kind
    )
