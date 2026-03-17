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


def _runtime_catalog_entries() -> tuple[ProviderCatalogEntry, ...]:
    entries: list[ProviderCatalogEntry] = []
    for requirement in list_provider_requirements():
        setup_notes: list[str] = []
        if requirement.required_env_keys:
            setup_notes.append(
                "Required secrets: " + ", ".join(requirement.required_env_keys)
            )
        if requirement.legacy_alias_keys:
            setup_notes.append(
                "Alias fallback supported: " + ", ".join(requirement.legacy_alias_keys)
            )
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
                required_env_keys=requirement.required_env_keys,
                optional_env_keys=requirement.optional_env_keys,
                setup_notes=tuple(setup_notes),
                command_paths=(
                    "portworld init",
                    "portworld config edit providers",
                    "portworld config show",
                    "portworld doctor --target local",
                    "portworld doctor --target gcp-cloud-run",
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
