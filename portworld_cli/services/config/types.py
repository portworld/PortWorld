from __future__ import annotations

from dataclasses import dataclass

from portworld_cli.envfile import EnvWriteResult
from portworld_cli.workspace.project_config import (
    AWSECSFargateConfig,
    AzureContainerAppsConfig,
    GCPCloudRunConfig,
    ProjectConfig,
)
from portworld_cli.workspace.session import SecretReadiness

@dataclass(frozen=True, slots=True)
class SecurityEditOptions:
    backend_profile: str | None
    cors_origins: str | None
    allowed_hosts: str | None
    bearer_token: str | None
    generate_bearer_token: bool
    clear_bearer_token: bool


@dataclass(frozen=True, slots=True)
class CloudEditOptions:
    project_mode: str | None
    runtime_source: str | None
    cloud_provider: str | None
    target: str | None
    project: str | None
    region: str | None
    service: str | None
    artifact_repo: str | None
    sql_instance: str | None
    database: str | None
    bucket: str | None
    min_instances: int | None
    max_instances: int | None
    concurrency: int | None
    cpu: str | None
    memory: str | None
    aws_region: str | None
    aws_service: str | None
    aws_vpc_id: str | None
    aws_subnet_ids: str | None
    azure_subscription: str | None
    azure_resource_group: str | None
    azure_region: str | None
    azure_environment: str | None
    azure_app: str | None


@dataclass(frozen=True, slots=True)
class SecuritySectionResult:
    backend_profile: str
    cors_origins: tuple[str, ...]
    allowed_hosts: tuple[str, ...]
    bearer_token: str


@dataclass(frozen=True, slots=True)
class CloudSectionResult:
    project_mode: str
    runtime_source: str
    cloud_provider: str | None
    preferred_target: str | None
    gcp_cloud_run: GCPCloudRunConfig
    aws_ecs_fargate: AWSECSFargateConfig
    azure_container_apps: AzureContainerAppsConfig


@dataclass(frozen=True, slots=True)
class ConfigWriteOutcome:
    project_config: ProjectConfig
    secret_readiness: SecretReadiness
    env_write_result: EnvWriteResult | None
