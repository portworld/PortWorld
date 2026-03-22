from __future__ import annotations

from pathlib import Path

from portworld_cli.output import format_key_value_lines
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
        f"cors_origins: {','.join(project_config.security.cors_origins)}",
        f"allowed_hosts: {','.join(project_config.security.allowed_hosts)}",
        f"preferred_target: {project_config.deploy.preferred_target or 'none'}",
        f"gcp_project_id: {project_config.deploy.gcp_cloud_run.project_id or 'unset'}",
        f"gcp_region: {project_config.deploy.gcp_cloud_run.region or 'unset'}",
        f"service_name: {project_config.deploy.gcp_cloud_run.service_name}",
        f"aws_region: {project_config.deploy.aws_ecs_fargate.region or 'unset'}",
        f"azure_region: {project_config.deploy.azure_container_apps.region or 'unset'}",
        "managed_target_execution: target-aware deploy/doctor support active",
        f"required_provider_secrets: {_required_secret_status(secret_readiness)}",
        f"missing_provider_secrets: {','.join(secret_readiness.missing_required_secret_keys) or 'none'}",
        f"required_provider_config: {_required_config_status(secret_readiness)}",
        f"missing_provider_config: {','.join(secret_readiness.missing_required_config_keys) or 'none'}",
        f"bearer_token: {presence_label(secret_readiness.bearer_token_present)}",
    )


def build_init_success_message(
    *,
    project_config: ProjectConfig,
    secret_readiness: SecretReadiness,
    env_path: Path | None,
    project_config_path: Path,
    backup_path: Path | None,
    extra_lines: tuple[str, ...] = (),
    next_steps: tuple[str, ...] = (
        "next: portworld doctor --target local",
        "next: portworld config show",
        "next: portworld deploy gcp-cloud-run",
    ),
) -> str:
    lines = list(
        build_init_review_lines(
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


def build_config_show_message(
    *,
    workspace_root: Path,
    project_config: ProjectConfig,
    secret_readiness: SecretReadiness,
    project_root: Path | None,
    env_path: Path | None,
    configured_runtime_source: str,
    effective_runtime_source: str,
    workspace_resolution_source: str,
    active_workspace_root: Path | None,
) -> str:
    pairs: list[tuple[str, object | None]] = [
        ("workspace_root", workspace_root),
        ("project_root", project_root),
        ("workspace_resolution_source", workspace_resolution_source),
        ("active_workspace_root", active_workspace_root),
        ("project_mode", project_config.project_mode),
        ("runtime_source", project_config.runtime_source or "unset"),
        ("configured_runtime_source", configured_runtime_source),
        ("effective_runtime_source", effective_runtime_source),
        ("cloud_provider", project_config.cloud_provider or "none"),
        ("realtime_provider", project_config.providers.realtime.provider),
        ("vision_memory", project_config.providers.vision.enabled),
        ("vision_provider", project_config.providers.vision.provider),
        ("realtime_tooling", project_config.providers.tooling.enabled),
        ("web_search_provider", project_config.providers.tooling.web_search_provider),
        ("backend_profile", normalize_backend_profile(project_config.security.backend_profile)),
        ("cors_origins", ",".join(project_config.security.cors_origins)),
        ("allowed_hosts", ",".join(project_config.security.allowed_hosts)),
        ("preferred_target", project_config.deploy.preferred_target or "none"),
        ("gcp_project_id", project_config.deploy.gcp_cloud_run.project_id or "unset"),
        ("gcp_region", project_config.deploy.gcp_cloud_run.region or "unset"),
        ("gcp_service_name", project_config.deploy.gcp_cloud_run.service_name),
        ("aws_region", project_config.deploy.aws_ecs_fargate.region or "unset"),
        ("aws_cluster_name", project_config.deploy.aws_ecs_fargate.cluster_name or "unset"),
        ("aws_service_name", project_config.deploy.aws_ecs_fargate.service_name or "unset"),
        ("aws_vpc_id", project_config.deploy.aws_ecs_fargate.vpc_id or "unset"),
        ("aws_subnet_ids", ",".join(project_config.deploy.aws_ecs_fargate.subnet_ids) or "unset"),
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
        ("managed_target_execution", "target-aware deploy/doctor support active"),
        ("env_path", env_path),
        ("required_provider_secrets", _required_secret_status(secret_readiness)),
        (
            "missing_provider_secrets",
            ",".join(secret_readiness.missing_required_secret_keys) or "none",
        ),
        ("required_provider_config", _required_config_status(secret_readiness)),
        (
            "missing_provider_config",
            ",".join(secret_readiness.missing_required_config_keys) or "none",
        ),
        ("bearer_token", presence_label(secret_readiness.bearer_token_present)),
    ]
    if effective_runtime_source == RUNTIME_SOURCE_PUBLISHED:
        pairs[20:20] = [
            ("published_release_tag", project_config.deploy.published_runtime.release_tag or "unset"),
            ("published_image_ref", project_config.deploy.published_runtime.image_ref or "unset"),
            ("published_host_port", project_config.deploy.published_runtime.host_port),
            ("compose_path", workspace_root / "docker-compose.yml"),
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
