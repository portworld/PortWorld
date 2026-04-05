from __future__ import annotations

from pathlib import Path

from portworld_cli.output import format_key_value_lines
from portworld_cli.targets import TARGET_GCP_CLOUD_RUN
from portworld_cli.workspace.project_config import ProjectConfig, RUNTIME_SOURCE_PUBLISHED
from portworld_cli.services.config.prompts import (
    normalize_backend_profile,
    presence_label,
)
from portworld_cli.workspace.session import SecretReadiness


def build_section_success_message(
    *,
    section_name: str,
    project_config: ProjectConfig,
    secret_readiness: SecretReadiness,
    env_path: Path | None,
    project_config_path: Path,
    backup_path: Path | None,
) -> str:
    lines = [
        f"section: {section_name}",
        f"project_mode: {project_config.project_mode}",
        f"runtime_source: {project_config.runtime_source or 'unset'}",
        f"cloud_provider: {project_config.cloud_provider or 'none'}",
        f"realtime_provider: {project_config.providers.realtime.provider}",
        f"vision_memory: {'yes' if project_config.providers.vision.enabled else 'no'}",
        f"vision_provider: {project_config.providers.vision.provider if project_config.providers.vision.enabled else 'disabled'}",
        f"realtime_tooling: {'yes' if project_config.providers.tooling.enabled else 'no'}",
        f"search_provider: {project_config.providers.tooling.web_search_provider if project_config.providers.tooling.enabled else 'disabled'}",
        f"backend_profile: {normalize_backend_profile(project_config.security.backend_profile)}",
        f"project_config_path: {project_config_path}",
        f"required_provider_secrets: {_required_secret_status(secret_readiness)}",
        f"missing_provider_secrets: {','.join(secret_readiness.missing_required_secret_keys) or 'none'}",
        f"required_provider_config: {_required_config_status(secret_readiness)}",
        f"missing_provider_config: {','.join(secret_readiness.missing_required_config_keys) or 'none'}",
        f"bearer_token: {presence_label(secret_readiness.bearer_token_present)}",
    ]
    if env_path is not None:
        lines.insert(7, f"env_path: {env_path}")
    if backup_path is not None:
        lines.append(f"backup_path: {backup_path}")
    return "\n".join(lines)


def build_init_review_lines(
    *,
    project_config: ProjectConfig,
    secret_readiness: SecretReadiness,
) -> tuple[str, ...]:
    return (
        f"project_mode: {project_config.project_mode}",
        f"runtime_source: {project_config.runtime_source or 'unset'}",
        f"cloud_provider: {project_config.cloud_provider or 'none'}",
        f"realtime_provider: {project_config.providers.realtime.provider}",
        f"vision_memory: {'yes' if project_config.providers.vision.enabled else 'no'}",
        f"vision_provider: {project_config.providers.vision.provider if project_config.providers.vision.enabled else 'disabled'}",
        f"realtime_tooling: {'yes' if project_config.providers.tooling.enabled else 'no'}",
        f"search_provider: {project_config.providers.tooling.web_search_provider if project_config.providers.tooling.enabled else 'disabled'}",
        f"backend_profile: {normalize_backend_profile(project_config.security.backend_profile)}",
        f"preferred_target: {project_config.deploy.preferred_target or 'none'}",
        f"gcp_project_id: {project_config.deploy.gcp_cloud_run.project_id or 'unset'}",
        f"gcp_region: {project_config.deploy.gcp_cloud_run.region or 'unset'}",
        f"gcp_service_name: {project_config.deploy.gcp_cloud_run.service_name}",
        f"aws_region: {project_config.deploy.aws_ecs_fargate.region or 'unset'}",
        f"aws_ecs_service: {project_config.deploy.aws_ecs_fargate.service_name or 'unset'}",
        f"azure_subscription_id: {project_config.deploy.azure_container_apps.subscription_id or 'unset'}",
        f"azure_resource_group: {project_config.deploy.azure_container_apps.resource_group or 'unset'}",
        f"azure_region: {project_config.deploy.azure_container_apps.region or 'unset'}",
        f"azure_environment_name: {project_config.deploy.azure_container_apps.environment_name or 'unset'}",
        f"azure_app_name: {project_config.deploy.azure_container_apps.app_name or 'unset'}",
        "managed_target_execution: target-aware deploy/doctor support active",
        f"required_provider_secrets: {_required_secret_status(secret_readiness)}",
        f"missing_provider_secrets: {','.join(secret_readiness.missing_required_secret_keys) or 'none'}",
        f"required_provider_config: {_required_config_status(secret_readiness)}",
        f"missing_provider_config: {','.join(secret_readiness.missing_required_config_keys) or 'none'}",
        f"bearer_token: {presence_label(secret_readiness.bearer_token_present)}",
    )


def build_init_confirmation_lines(
    *,
    project_config: ProjectConfig,
    secret_readiness: SecretReadiness,
) -> tuple[str, ...]:
    lines: list[str] = [
        f"project_mode: {project_config.project_mode}",
        f"runtime_source: {project_config.runtime_source or 'unset'}",
        f"cloud_provider: {project_config.cloud_provider or 'none'}",
        f"realtime_provider: {project_config.providers.realtime.provider}",
        f"vision_memory: {'yes' if project_config.providers.vision.enabled else 'no'}",
        f"realtime_tooling: {'yes' if project_config.providers.tooling.enabled else 'no'}",
    ]
    if project_config.project_mode == "managed":
        lines.append(f"preferred_target: {project_config.deploy.preferred_target or 'none'}")
    if secret_readiness.missing_required_secret_keys:
        lines.append(
            f"missing_provider_secrets: {','.join(secret_readiness.missing_required_secret_keys)}"
        )
    else:
        lines.append("missing_provider_secrets: none")
    if secret_readiness.missing_required_config_keys:
        lines.append(
            f"missing_provider_config: {','.join(secret_readiness.missing_required_config_keys)}"
        )
    else:
        lines.append("missing_provider_config: none")
    lines.append(f"bearer_token: {presence_label(secret_readiness.bearer_token_present)}")
    return tuple(lines)


def build_init_success_message(
    *,
    project_config: ProjectConfig,
    secret_readiness: SecretReadiness,
    env_path: Path | None,
    project_config_path: Path,
    backup_path: Path | None,
    extra_lines: tuple[str, ...] = (),
    next_steps: tuple[str, ...] | None = None,
) -> str:
    if next_steps is None:
        next_steps = (
            "next: portworld doctor --target local",
            "next: portworld config show",
            f"next: {default_managed_deploy_command(project_config)}",
        )
    lines = list(
        build_init_confirmation_lines(
            project_config=project_config,
            secret_readiness=secret_readiness,
        )
    )
    lines.append(f"project_config_path: {project_config_path}")
    if env_path is not None:
        lines.append(f"env_path: {env_path}")
    if backup_path is not None:
        lines.append(f"backup_path: {backup_path}")
    lines.extend(line for line in extra_lines if line)
    lines.extend(next_steps)
    return "\n".join(lines)


def default_managed_target(project_config: ProjectConfig) -> str:
    return project_config.deploy.preferred_target or TARGET_GCP_CLOUD_RUN


def default_managed_deploy_command(project_config: ProjectConfig) -> str:
    return f"portworld deploy {default_managed_target(project_config)}"


def build_config_show_message(
    *,
    workspace_root: Path,
    project_config: ProjectConfig,
    secret_readiness: SecretReadiness,
    project_root: Path | None,
    env_path: Path | None,
    derived_from_legacy: bool,
    configured_runtime_source: str | None,
    effective_runtime_source: str,
    runtime_source_derived_from_legacy: bool,
    workspace_resolution_source: str,
    active_workspace_root: Path | None,
) -> str:
    pairs: list[tuple[str, object | None]] = [
        ("project_mode", project_config.project_mode),
        ("runtime_source", project_config.runtime_source or "unset"),
        ("cloud_provider", project_config.cloud_provider or "none"),
        ("preferred_target", project_config.deploy.preferred_target or "none"),
        ("realtime", _humanize_realtime_provider(project_config.providers.realtime.provider)),
        (
            "vision_memory",
            _humanize_optional_provider(
                enabled=project_config.providers.vision.enabled,
                provider_id=project_config.providers.vision.provider,
                suffix="Vision",
            ),
        ),
        (
            "realtime_tooling",
            _humanize_optional_provider(
                enabled=project_config.providers.tooling.enabled,
                provider_id=project_config.providers.tooling.web_search_provider,
                suffix="Search",
            ),
        ),
        ("backend_profile", normalize_backend_profile(project_config.security.backend_profile)),
        ("gcp_project_id", project_config.deploy.gcp_cloud_run.project_id or "unset"),
        ("gcp_region", project_config.deploy.gcp_cloud_run.region or "unset"),
        ("gcp_service_name", project_config.deploy.gcp_cloud_run.service_name),
        ("aws_region", project_config.deploy.aws_ecs_fargate.region or "unset"),
        ("aws_ecs_service", project_config.deploy.aws_ecs_fargate.service_name or "unset"),
        (
            "azure_subscription_id",
            project_config.deploy.azure_container_apps.subscription_id or "unset",
        ),
        (
            "azure_resource_group",
            project_config.deploy.azure_container_apps.resource_group or "unset",
        ),
        ("azure_region", project_config.deploy.azure_container_apps.region or "unset"),
        (
            "azure_environment_name",
            project_config.deploy.azure_container_apps.environment_name or "unset",
        ),
        ("azure_app_name", project_config.deploy.azure_container_apps.app_name or "unset"),
        ("credentials", _humanize_credentials(secret_readiness)),
        ("bearer_token", presence_label(secret_readiness.bearer_token_present)),
    ]
    if effective_runtime_source == RUNTIME_SOURCE_PUBLISHED:
        pairs[10:10] = [
            ("published_release_tag", project_config.deploy.published_runtime.release_tag or "unset"),
            ("published_image_ref", project_config.deploy.published_runtime.image_ref or "unset"),
            ("published_host_port", project_config.deploy.published_runtime.host_port),
        ]
    return format_key_value_lines(*pairs)


def _required_secret_status(secret_readiness: SecretReadiness) -> str:
    if not secret_readiness.required_secret_keys:
        return "none_required"
    parts: list[str] = []
    for key in secret_readiness.required_secret_keys:
        parts.append(f"{key}:{presence_label(secret_readiness.key_presence.get(key))}")
    return ",".join(parts)


def _required_config_status(secret_readiness: SecretReadiness) -> str:
    if not secret_readiness.required_config_keys:
        return "none_required"
    parts: list[str] = []
    for key in secret_readiness.required_config_keys:
        parts.append(f"{key}:{presence_label(secret_readiness.config_key_presence.get(key))}")
    return ",".join(parts)


def _humanize_credentials(secret_readiness: SecretReadiness) -> str:
    if secret_readiness.missing_required_secret_keys or secret_readiness.missing_required_config_keys:
        missing = [
            *_humanize_required_keys(secret_readiness.missing_required_secret_keys),
            *_humanize_required_keys(secret_readiness.missing_required_config_keys),
        ]
        return f"missing {', '.join(missing)}"
    return "all required credentials present"


def _humanize_required_keys(keys: tuple[str, ...]) -> list[str]:
    labels: list[str] = []
    for key in keys:
        if "TAVILY" in key:
            labels.append("Tavily search credentials")
        elif key.startswith("VISION_"):
            labels.append("vision provider credentials")
        elif "OPENAI" in key:
            labels.append("OpenAI credentials")
        elif "GEMINI" in key:
            labels.append("Gemini credentials")
        else:
            labels.append(key.lower())
    deduped: list[str] = []
    for label in labels:
        if label not in deduped:
            deduped.append(label)
    return deduped


def _humanize_realtime_provider(provider_id: str) -> str:
    if provider_id == "gemini_live":
        return "Gemini Live"
    if provider_id == "openai":
        return "OpenAI Realtime"
    return provider_id.replace("_", " ").title()


def _humanize_optional_provider(
    *,
    enabled: bool,
    provider_id: str | None,
    suffix: str,
) -> str:
    if not enabled or provider_id is None:
        return "disabled"
    label = provider_id.replace("_", " ").title()
    if suffix.lower() not in label.lower():
        label = f"{label} {suffix}"
    return f"enabled ({label})"
