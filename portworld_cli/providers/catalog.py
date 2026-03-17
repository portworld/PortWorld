from __future__ import annotations

from dataclasses import dataclass


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


PROVIDER_CATALOG: tuple[ProviderCatalogEntry, ...] = (
    ProviderCatalogEntry(
        id="gcp",
        display_name="GCP Cloud Run",
        kind="cloud",
        summary="Managed deployment path for PortWorld on Google Cloud Run.",
        default=True,
        aliases=("gcp-cloud-run",),
        capability_tags=("deploy", "status", "logs", "update_deploy"),
        supported_targets=("gcp-cloud-run",),
        required_clis=("gcloud",),
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
    ProviderCatalogEntry(
        id="aws",
        display_name="AWS ECS/Fargate",
        kind="cloud",
        summary="Managed deployment path for PortWorld on AWS ECS/Fargate behind ALB HTTPS.",
        default=False,
        aliases=("aws-ecs-fargate",),
        capability_tags=("deploy", "status"),
        supported_targets=("aws-ecs-fargate",),
        required_clis=("aws",),
        setup_notes=(
            "Configure AWS credentials with `aws configure` before managed commands.",
            "Provide VPC/subnets, ECS cluster/service names, and an ISSUED ACM certificate ARN.",
            "Use `portworld doctor --target aws-ecs-fargate` before first deploy.",
        ),
        command_paths=(
            "portworld doctor --target aws-ecs-fargate",
            "portworld deploy aws-ecs-fargate",
            "portworld status",
        ),
    ),
    ProviderCatalogEntry(
        id="azure",
        display_name="Azure Container Apps",
        kind="cloud",
        summary="Managed deployment path for PortWorld on Azure Container Apps with provider FQDN HTTPS.",
        default=False,
        aliases=("azure-container-apps",),
        capability_tags=("deploy", "status"),
        supported_targets=("azure-container-apps",),
        required_clis=("az",),
        setup_notes=(
            "Authenticate with `az login` before managed commands.",
            "Provide subscription/resource group/environment/app and managed storage/database inputs.",
            "Use `portworld doctor --target azure-container-apps` before first deploy.",
        ),
        command_paths=(
            "portworld doctor --target azure-container-apps",
            "portworld deploy azure-container-apps",
            "portworld status",
        ),
    ),
    ProviderCatalogEntry(
        id="openai",
        display_name="OpenAI Realtime",
        kind="realtime",
        summary="Realtime session provider used by the current backend runtime.",
        default=True,
        capability_tags=("realtime_sessions",),
        required_env_keys=("OPENAI_API_KEY",),
        setup_notes=(
            "Required for all current realtime websocket sessions.",
            "Configured through `portworld init` or `portworld config edit providers`.",
        ),
        command_paths=(
            "portworld init",
            "portworld config edit providers",
            "portworld config show",
        ),
    ),
    ProviderCatalogEntry(
        id="mistral",
        display_name="Mistral-Compatible Vision",
        kind="vision",
        summary="Vision-memory provider used when visual memory is enabled.",
        default=True,
        capability_tags=("vision_memory",),
        required_env_keys=("VISION_PROVIDER_API_KEY", "MISTRAL_API_KEY"),
        optional_env_keys=("VISION_PROVIDER_BASE_URL", "MISTRAL_BASE_URL"),
        setup_notes=(
            "Only required when `VISION_MEMORY_ENABLED=true`.",
            "Legacy `MISTRAL_API_KEY` and `MISTRAL_BASE_URL` aliases remain supported.",
            "The API key must be a provider credential, not a model id.",
        ),
        command_paths=(
            "portworld init",
            "portworld config edit providers",
            "portworld config show",
        ),
    ),
    ProviderCatalogEntry(
        id="tavily",
        display_name="Tavily Web Search",
        kind="search",
        summary="Web-search provider used by the optional realtime tooling path.",
        default=True,
        capability_tags=("web_search",),
        required_env_keys=("TAVILY_API_KEY",),
        optional_env_keys=("TAVILY_BASE_URL",),
        setup_notes=(
            "Only used when realtime tooling is enabled and web search should be available.",
            "If tooling is enabled without `TAVILY_API_KEY`, the backend still runs but omits `web_search`.",
        ),
        command_paths=(
            "portworld init",
            "portworld config edit providers",
            "portworld config show",
        ),
    ),
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
        values.append(entry.id)
        values.extend(alias for alias in entry.aliases if alias not in values)
    return tuple(values)
