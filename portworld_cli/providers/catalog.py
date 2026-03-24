from __future__ import annotations

from dataclasses import dataclass

from backend.core.provider_requirements import list_provider_requirements


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


_DEFAULT_PROVIDER_IDS: dict[str, str] = {
    "realtime": "openai",
    "vision": "mistral",
    "search": "tavily",
}


def _merge_key_sets(*key_sets: tuple[str, ...]) -> tuple[str, ...]:
    ordered: list[str] = []
    for key_set in key_sets:
        for key in key_set:
            if key not in ordered:
                ordered.append(key)
    return tuple(ordered)


def _runtime_catalog_entries() -> tuple[ProviderCatalogEntry, ...]:
    entries: list[ProviderCatalogEntry] = []
    for requirement in list_provider_requirements():
        setup_notes: list[str] = []
        required_secrets = _merge_key_sets(
            requirement.required_secret_env_keys,
            requirement.secret_binding.required_env_keys,
        )
        optional_secrets = _merge_key_sets(
            requirement.optional_secret_env_keys,
            requirement.secret_binding.optional_env_keys,
        )
        required_config = requirement.required_non_secret_env_keys
        optional_config = requirement.optional_non_secret_env_keys
        if required_secrets:
            setup_notes.append("Required secrets: " + ", ".join(required_secrets))
        if required_config:
            setup_notes.append("Required config: " + ", ".join(required_config))
        if optional_secrets:
            setup_notes.append("Optional secrets: " + ", ".join(optional_secrets))
        if optional_config:
            setup_notes.append("Optional config: " + ", ".join(optional_config))
        setup_notes.append(
            "Configure with `portworld init` or `portworld config edit providers`."
        )
        entries.append(
            ProviderCatalogEntry(
                id=requirement.provider_id,
                display_name=requirement.display_name,
                kind=requirement.kind,
                summary=requirement.summary,
                default=_DEFAULT_PROVIDER_IDS.get(requirement.kind) == requirement.provider_id,
                capability_tags=requirement.capability_tags,
                required_env_keys=_merge_key_sets(
                    requirement.required_env_keys,
                    requirement.required_non_secret_env_keys,
                ),
                optional_env_keys=_merge_key_sets(
                    requirement.optional_env_keys,
                    requirement.optional_secret_env_keys,
                    requirement.optional_non_secret_env_keys,
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
        summary="Managed deployment path for PortWorld on AWS ECS/Fargate with CloudFront, ALB, S3 memory storage, and Postgres operational metadata.",
        default=False,
        aliases=("aws-ecs-fargate",),
        capability_tags=("deploy", "status", "logs", "update_deploy"),
        supported_targets=("aws-ecs-fargate",),
        required_clis=("aws",),
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
    ProviderCatalogEntry(
        id="azure",
        display_name="Azure Container Apps",
        kind="cloud",
        summary="Managed deployment path for PortWorld on Azure Container Apps with Blob memory storage and Postgres operational metadata.",
        default=False,
        aliases=("azure-container-apps",),
        capability_tags=("deploy", "status", "logs", "update_deploy"),
        supported_targets=("azure-container-apps",),
        required_clis=("az",),
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
        values.append(entry.id)
        values.extend(alias for alias in entry.aliases if alias not in values)
    return tuple(values)
