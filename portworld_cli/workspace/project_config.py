from __future__ import annotations

from dataclasses import dataclass, field
import json
import tempfile
from pathlib import Path
from typing import Any, Mapping

from portworld_cli.deploy_artifacts import (
    IMAGE_SOURCE_MODE_PUBLISHED_RELEASE,
    PUBLISHED_ARTIFACT_REPOSITORY_SUFFIX,
)
from portworld_cli.targets import (
    CLOUD_PROVIDER_AWS,
    CLOUD_PROVIDER_AZURE,
    CLOUD_PROVIDER_GCP,
    MANAGED_TARGETS_BY_PROVIDER,
    MANAGED_TARGETS,
    TARGET_AWS_ECS_FARGATE,
    TARGET_AZURE_CONTAINER_APPS,
    TARGET_GCP_CLOUD_RUN,
)


SCHEMA_VERSION = 4
PROJECT_MODE_LOCAL = "local"
PROJECT_MODE_MANAGED = "managed"
RUNTIME_SOURCE_SOURCE = "source"
RUNTIME_SOURCE_PUBLISHED = "published"
GCP_CLOUD_RUN_TARGET = TARGET_GCP_CLOUD_RUN

DEFAULT_REALTIME_PROVIDER = "openai"
DEFAULT_VISION_PROVIDER = "mistral"
DEFAULT_WEB_SEARCH_PROVIDER = "tavily"
DEFAULT_BACKEND_PROFILE = "development"
DEFAULT_CORS_ORIGINS: tuple[str, ...] = ("*",)
DEFAULT_ALLOWED_HOSTS: tuple[str, ...] = ("*",)

DEFAULT_GCP_REGION = "us-central1"
DEFAULT_GCP_SERVICE_NAME = "portworld-backend"
DEFAULT_GCP_ARTIFACT_REPOSITORY = "portworld"
DEFAULT_GCP_SQL_INSTANCE_NAME = "portworld-pg"
DEFAULT_GCP_DATABASE_NAME = "portworld"
DEFAULT_GCP_MIN_INSTANCES = 1
DEFAULT_GCP_MAX_INSTANCES = 10
DEFAULT_GCP_CONCURRENCY = 10
DEFAULT_GCP_CPU = "1"
DEFAULT_GCP_MEMORY = "1Gi"
DEFAULT_PUBLISHED_HOST_PORT = 8080


class ProjectConfigError(RuntimeError):
    """Base error for CLI-managed project config."""


class ProjectConfigDecodeError(ProjectConfigError):
    """Raised when project config is not valid JSON."""


class ProjectConfigTypeError(ProjectConfigError):
    """Raised when project config has an invalid JSON shape."""


class ProjectConfigVersionError(ProjectConfigError):
    """Raised when project config uses an unsupported schema version."""


@dataclass(frozen=True, slots=True)
class LoadedProjectConfig:
    config: "ProjectConfig"
    schema_version: int
    runtime_source_explicit: bool


@dataclass(frozen=True, slots=True)
class RealtimeProviderConfig:
    provider: str = DEFAULT_REALTIME_PROVIDER

    def to_payload(self) -> dict[str, Any]:
        return {"provider": self.provider}


@dataclass(frozen=True, slots=True)
class VisionProviderConfig:
    enabled: bool = False
    provider: str = DEFAULT_VISION_PROVIDER

    def to_payload(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
        }


@dataclass(frozen=True, slots=True)
class ToolingConfig:
    enabled: bool = False
    web_search_provider: str = DEFAULT_WEB_SEARCH_PROVIDER

    def to_payload(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "web_search_provider": self.web_search_provider,
        }


@dataclass(frozen=True, slots=True)
class ProvidersConfig:
    realtime: RealtimeProviderConfig = field(default_factory=RealtimeProviderConfig)
    vision: VisionProviderConfig = field(default_factory=VisionProviderConfig)
    tooling: ToolingConfig = field(default_factory=ToolingConfig)

    def to_payload(self) -> dict[str, Any]:
        return {
            "realtime": self.realtime.to_payload(),
            "vision": self.vision.to_payload(),
            "tooling": self.tooling.to_payload(),
        }


@dataclass(frozen=True, slots=True)
class SecurityConfig:
    backend_profile: str = DEFAULT_BACKEND_PROFILE
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS
    allowed_hosts: tuple[str, ...] = DEFAULT_ALLOWED_HOSTS

    def to_payload(self) -> dict[str, Any]:
        return {
            "backend_profile": self.backend_profile,
            "cors_origins": list(self.cors_origins),
            "allowed_hosts": list(self.allowed_hosts),
        }


@dataclass(frozen=True, slots=True)
class GCPCloudRunConfig:
    project_id: str | None = None
    region: str | None = None
    service_name: str = DEFAULT_GCP_SERVICE_NAME
    artifact_repository: str = DEFAULT_GCP_ARTIFACT_REPOSITORY
    sql_instance_name: str = DEFAULT_GCP_SQL_INSTANCE_NAME
    database_name: str = DEFAULT_GCP_DATABASE_NAME
    bucket_name: str | None = None
    min_instances: int = DEFAULT_GCP_MIN_INSTANCES
    max_instances: int = DEFAULT_GCP_MAX_INSTANCES
    concurrency: int = DEFAULT_GCP_CONCURRENCY
    cpu: str = DEFAULT_GCP_CPU
    memory: str = DEFAULT_GCP_MEMORY

    def to_payload(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "region": self.region,
            "service_name": self.service_name,
            "artifact_repository": self.artifact_repository,
            "sql_instance_name": self.sql_instance_name,
            "database_name": self.database_name,
            "bucket_name": self.bucket_name,
            "min_instances": self.min_instances,
            "max_instances": self.max_instances,
            "concurrency": self.concurrency,
            "cpu": self.cpu,
            "memory": self.memory,
        }


@dataclass(frozen=True, slots=True)
class AWSECSFargateConfig:
    region: str | None = None
    cluster_name: str | None = None
    service_name: str | None = None
    vpc_id: str | None = None
    subnet_ids: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "region": self.region,
            "cluster_name": self.cluster_name,
            "service_name": self.service_name,
            "vpc_id": self.vpc_id,
            "subnet_ids": list(self.subnet_ids),
        }


@dataclass(frozen=True, slots=True)
class AzureContainerAppsConfig:
    subscription_id: str | None = None
    resource_group: str | None = None
    region: str | None = None
    environment_name: str | None = None
    app_name: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "subscription_id": self.subscription_id,
            "resource_group": self.resource_group,
            "region": self.region,
            "environment_name": self.environment_name,
            "app_name": self.app_name,
        }


@dataclass(frozen=True, slots=True)
class PublishedRuntimeConfig:
    release_tag: str | None = None
    image_ref: str | None = None
    host_port: int = DEFAULT_PUBLISHED_HOST_PORT

    def to_payload(self) -> dict[str, Any]:
        return {
            "release_tag": self.release_tag,
            "image_ref": self.image_ref,
            "host_port": self.host_port,
        }


@dataclass(frozen=True, slots=True)
class DeployConfig:
    preferred_target: str | None = None
    gcp_cloud_run: GCPCloudRunConfig = field(default_factory=GCPCloudRunConfig)
    aws_ecs_fargate: AWSECSFargateConfig = field(default_factory=AWSECSFargateConfig)
    azure_container_apps: AzureContainerAppsConfig = field(default_factory=AzureContainerAppsConfig)
    published_runtime: PublishedRuntimeConfig = field(default_factory=PublishedRuntimeConfig)

    def to_payload(self) -> dict[str, Any]:
        return {
            "preferred_target": self.preferred_target,
            "gcp_cloud_run": self.gcp_cloud_run.to_payload(),
            "aws_ecs_fargate": self.aws_ecs_fargate.to_payload(),
            "azure_container_apps": self.azure_container_apps.to_payload(),
            "published_runtime": self.published_runtime.to_payload(),
        }


@dataclass(frozen=True, slots=True)
class ProjectConfig:
    schema_version: int = SCHEMA_VERSION
    project_mode: str = PROJECT_MODE_LOCAL
    runtime_source: str | None = None
    cloud_provider: str | None = None
    providers: ProvidersConfig = field(default_factory=ProvidersConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    deploy: DeployConfig = field(default_factory=DeployConfig)

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project_mode": self.project_mode,
            "runtime_source": self.runtime_source,
            "cloud_provider": self.cloud_provider,
            "providers": self.providers.to_payload(),
            "security": self.security.to_payload(),
            "deploy": self.deploy.to_payload(),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ProjectConfig":
        schema_version = _read_int(payload, "schema_version", default=SCHEMA_VERSION)
        if schema_version not in {1, 2, 3, SCHEMA_VERSION}:
            raise ProjectConfigVersionError(
                f"Unsupported .portworld/project.json schema_version: {schema_version}."
            )

        project_mode = _read_string(
            payload,
            "project_mode",
            default=PROJECT_MODE_LOCAL,
            allowed={PROJECT_MODE_LOCAL, PROJECT_MODE_MANAGED},
        )
        runtime_source = _read_optional_string(
            payload,
            "runtime_source",
            allowed={RUNTIME_SOURCE_SOURCE, RUNTIME_SOURCE_PUBLISHED},
        )
        providers_payload = _read_object(payload, "providers", default={})
        security_payload = _read_object(payload, "security", default={})
        deploy_payload = _read_object(payload, "deploy", default={})
        gcp_payload = _read_object(deploy_payload, "gcp_cloud_run", default={})
        aws_payload = _read_object(deploy_payload, "aws_ecs_fargate", default={})
        azure_payload = _read_object(deploy_payload, "azure_container_apps", default={})
        published_runtime_payload = _read_object(
            deploy_payload,
            "published_runtime",
            default={},
        )

        preferred_target = _read_optional_string(
            deploy_payload,
            "preferred_target",
            allowed=set(MANAGED_TARGETS),
        )
        cloud_provider = _read_optional_string(
            payload,
            "cloud_provider",
            allowed={CLOUD_PROVIDER_GCP, CLOUD_PROVIDER_AWS, CLOUD_PROVIDER_AZURE},
        )
        cloud_provider, preferred_target = _normalize_cloud_selection(
            project_mode=project_mode,
            cloud_provider=cloud_provider,
            preferred_target=preferred_target,
        )

        return cls(
            schema_version=SCHEMA_VERSION,
            project_mode=project_mode,
            runtime_source=runtime_source,
            cloud_provider=cloud_provider,
            providers=ProvidersConfig(
                realtime=RealtimeProviderConfig(
                    provider=_read_string(
                        _read_object(providers_payload, "realtime", default={}),
                        "provider",
                        default=DEFAULT_REALTIME_PROVIDER,
                    ),
                ),
                vision=VisionProviderConfig(
                    enabled=_read_bool(
                        _read_object(providers_payload, "vision", default={}),
                        "enabled",
                        default=False,
                    ),
                    provider=_read_string(
                        _read_object(providers_payload, "vision", default={}),
                        "provider",
                        default=DEFAULT_VISION_PROVIDER,
                    ),
                ),
                tooling=ToolingConfig(
                    enabled=_read_bool(
                        _read_object(providers_payload, "tooling", default={}),
                        "enabled",
                        default=False,
                    ),
                    web_search_provider=_read_string(
                        _read_object(providers_payload, "tooling", default={}),
                        "web_search_provider",
                        default=DEFAULT_WEB_SEARCH_PROVIDER,
                    ),
                ),
            ),
            security=SecurityConfig(
                backend_profile=_read_string(
                    security_payload,
                    "backend_profile",
                    default=DEFAULT_BACKEND_PROFILE,
                ),
                cors_origins=_read_string_list(
                    security_payload,
                    "cors_origins",
                    default=DEFAULT_CORS_ORIGINS,
                ),
                allowed_hosts=_read_string_list(
                    security_payload,
                    "allowed_hosts",
                    default=DEFAULT_ALLOWED_HOSTS,
                ),
            ),
            deploy=DeployConfig(
                preferred_target=preferred_target,
                gcp_cloud_run=GCPCloudRunConfig(
                    project_id=_read_optional_string(gcp_payload, "project_id"),
                    region=_read_optional_string(gcp_payload, "region"),
                    service_name=_read_string(
                        gcp_payload,
                        "service_name",
                        default=DEFAULT_GCP_SERVICE_NAME,
                    ),
                    artifact_repository=_read_string(
                        gcp_payload,
                        "artifact_repository",
                        default=DEFAULT_GCP_ARTIFACT_REPOSITORY,
                    ),
                    sql_instance_name=_read_string(
                        gcp_payload,
                        "sql_instance_name",
                        default=DEFAULT_GCP_SQL_INSTANCE_NAME,
                    ),
                    database_name=_read_string(
                        gcp_payload,
                        "database_name",
                        default=DEFAULT_GCP_DATABASE_NAME,
                    ),
                    bucket_name=_read_optional_string(gcp_payload, "bucket_name"),
                    min_instances=_read_int(
                        gcp_payload,
                        "min_instances",
                        default=DEFAULT_GCP_MIN_INSTANCES,
                    ),
                    max_instances=_read_int(
                        gcp_payload,
                        "max_instances",
                        default=DEFAULT_GCP_MAX_INSTANCES,
                    ),
                    concurrency=_read_int(
                        gcp_payload,
                        "concurrency",
                        default=DEFAULT_GCP_CONCURRENCY,
                    ),
                    cpu=_read_string(gcp_payload, "cpu", default=DEFAULT_GCP_CPU),
                    memory=_read_string(
                        gcp_payload,
                        "memory",
                        default=DEFAULT_GCP_MEMORY,
                    ),
                ),
                aws_ecs_fargate=AWSECSFargateConfig(
                    region=_read_optional_string(aws_payload, "region"),
                    cluster_name=_read_optional_string(aws_payload, "cluster_name"),
                    service_name=_read_optional_string(aws_payload, "service_name"),
                    vpc_id=_read_optional_string(aws_payload, "vpc_id"),
                    subnet_ids=_read_string_list(
                        aws_payload,
                        "subnet_ids",
                        default=(),
                    ),
                ),
                azure_container_apps=AzureContainerAppsConfig(
                    subscription_id=_read_optional_string(azure_payload, "subscription_id"),
                    resource_group=_read_optional_string(azure_payload, "resource_group"),
                    region=_read_optional_string(azure_payload, "region"),
                    environment_name=_read_optional_string(azure_payload, "environment_name"),
                    app_name=_read_optional_string(azure_payload, "app_name"),
                ),
                published_runtime=PublishedRuntimeConfig(
                    release_tag=_read_optional_string(
                        published_runtime_payload,
                        "release_tag",
                    ),
                    image_ref=_read_optional_string(
                        published_runtime_payload,
                        "image_ref",
                    ),
                    host_port=_read_int(
                        published_runtime_payload,
                        "host_port",
                        default=DEFAULT_PUBLISHED_HOST_PORT,
                    ),
                ),
            ),
        )


def load_project_config(path: Path) -> ProjectConfig | None:
    record = load_project_config_record(path)
    return None if record is None else record.config


def load_project_config_record(path: Path) -> LoadedProjectConfig | None:
    if not path.exists():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProjectConfigDecodeError(
            f"Failed to parse CLI project config file: {path}"
        ) from exc

    if not isinstance(payload, dict):
        raise ProjectConfigTypeError(
            f"CLI project config file must contain a JSON object: {path}"
        )
    config = ProjectConfig.from_payload(payload)
    return LoadedProjectConfig(
        config=config,
        schema_version=_read_int(payload, "schema_version", default=SCHEMA_VERSION),
        runtime_source_explicit=payload.get("runtime_source") is not None,
    )


def write_project_config(path: Path, config: ProjectConfig) -> None:
    if config.runtime_source is None:
        raise ProjectConfigTypeError(
            "CLI project config must set runtime_source before it can be written."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    tmp_path = Path(tmp_name)
    try:
        with open(fd, "w", encoding="utf-8") as handle:
            json.dump(config.to_payload(), handle, ensure_ascii=True, indent=2)
            handle.write("\n")
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def derive_project_config(
    *,
    env_values: Mapping[str, str],
    deploy_state: Mapping[str, Any] | None = None,
    default_runtime_source: str = RUNTIME_SOURCE_SOURCE,
) -> ProjectConfig:
    remembered_state = deploy_state or {}
    remembered_project_id = _coerce_optional_text(remembered_state.get("project_id"))
    remembered_region = _coerce_optional_text(remembered_state.get("region"))
    remembered_service_name = _coerce_optional_text(remembered_state.get("service_name"))
    remembered_artifact_repository = _resolve_remembered_artifact_repository(
        artifact_repository_base=_coerce_optional_text(
            remembered_state.get("artifact_repository_base")
        ),
        artifact_repository=_coerce_optional_text(
            remembered_state.get("artifact_repository")
        ),
        image_source_mode=_coerce_optional_text(
            remembered_state.get("image_source_mode")
        ),
    )
    remembered_sql_instance = _coerce_optional_text(
        remembered_state.get("cloud_sql_instance")
    )
    remembered_database_name = _coerce_optional_text(remembered_state.get("database_name"))
    remembered_bucket_name = _coerce_optional_text(remembered_state.get("bucket_name"))
    remembered_target_exists = any(
        value is not None
        for value in (
            remembered_project_id,
            remembered_region,
            remembered_service_name,
            remembered_bucket_name,
        )
    )

    return ProjectConfig(
        project_mode=(
            PROJECT_MODE_MANAGED if remembered_target_exists else PROJECT_MODE_LOCAL
        ),
        runtime_source=default_runtime_source,
        cloud_provider=(
            CLOUD_PROVIDER_GCP if remembered_target_exists else None
        ),
        providers=ProvidersConfig(
            realtime=RealtimeProviderConfig(
                provider=_normalized_provider(
                    env_values.get("REALTIME_PROVIDER"),
                    default=DEFAULT_REALTIME_PROVIDER,
                )
            ),
            vision=VisionProviderConfig(
                enabled=_parse_bool_string(
                    env_values.get("VISION_MEMORY_ENABLED", "false")
                ),
                provider=_normalized_provider(
                    env_values.get("VISION_MEMORY_PROVIDER"),
                    default=DEFAULT_VISION_PROVIDER,
                ),
            ),
            tooling=ToolingConfig(
                enabled=_parse_bool_string(
                    env_values.get("REALTIME_TOOLING_ENABLED", "false")
                ),
                web_search_provider=_normalized_provider(
                    env_values.get("REALTIME_WEB_SEARCH_PROVIDER"),
                    default=DEFAULT_WEB_SEARCH_PROVIDER,
                ),
            ),
        ),
        security=SecurityConfig(
            backend_profile=(
                _coerce_optional_text(env_values.get("BACKEND_PROFILE"))
                or DEFAULT_BACKEND_PROFILE
            ),
            cors_origins=_parse_csv_values(
                env_values.get("CORS_ORIGINS"),
                default=DEFAULT_CORS_ORIGINS,
            ),
            allowed_hosts=_parse_csv_values(
                env_values.get("BACKEND_ALLOWED_HOSTS"),
                default=DEFAULT_ALLOWED_HOSTS,
            ),
        ),
        deploy=DeployConfig(
            preferred_target=(
                GCP_CLOUD_RUN_TARGET if remembered_target_exists else None
            ),
            gcp_cloud_run=GCPCloudRunConfig(
                project_id=remembered_project_id,
                region=remembered_region,
                service_name=remembered_service_name or DEFAULT_GCP_SERVICE_NAME,
                artifact_repository=(
                    remembered_artifact_repository or DEFAULT_GCP_ARTIFACT_REPOSITORY
                ),
                sql_instance_name=remembered_sql_instance or DEFAULT_GCP_SQL_INSTANCE_NAME,
                database_name=remembered_database_name or DEFAULT_GCP_DATABASE_NAME,
                bucket_name=remembered_bucket_name,
                min_instances=DEFAULT_GCP_MIN_INSTANCES,
                max_instances=DEFAULT_GCP_MAX_INSTANCES,
                concurrency=DEFAULT_GCP_CONCURRENCY,
                cpu=DEFAULT_GCP_CPU,
                memory=DEFAULT_GCP_MEMORY,
            ),
        ),
    )


def build_env_overrides_from_project_config(
    config: ProjectConfig,
) -> dict[str, str]:
    return {
        "REALTIME_PROVIDER": config.providers.realtime.provider,
        "VISION_MEMORY_ENABLED": _bool_env_value(config.providers.vision.enabled),
        "VISION_MEMORY_PROVIDER": config.providers.vision.provider,
        "REALTIME_TOOLING_ENABLED": _bool_env_value(config.providers.tooling.enabled),
        "REALTIME_WEB_SEARCH_PROVIDER": config.providers.tooling.web_search_provider,
        "BACKEND_PROFILE": config.security.backend_profile,
        "CORS_ORIGINS": ",".join(config.security.cors_origins),
        "BACKEND_ALLOWED_HOSTS": ",".join(config.security.allowed_hosts),
    }


def _read_object(
    payload: Mapping[str, Any],
    key: str,
    *,
    default: Mapping[str, Any],
) -> Mapping[str, Any]:
    value = payload.get(key, default)
    if not isinstance(value, dict):
        raise ProjectConfigTypeError(
            f".portworld/project.json field '{key}' must be a JSON object."
        )
    return value


def _read_string(
    payload: Mapping[str, Any],
    key: str,
    *,
    default: str,
    allowed: set[str] | None = None,
) -> str:
    value = payload.get(key)
    if value is None:
        text = default
    elif not isinstance(value, str):
        raise ProjectConfigTypeError(
            f".portworld/project.json field '{key}' must be a string."
        )
    else:
        text = value.strip() or default

    if allowed is not None and text not in allowed:
        allowed_values = ", ".join(sorted(allowed))
        raise ProjectConfigTypeError(
            f".portworld/project.json field '{key}' must be one of: {allowed_values}."
        )
    return text


def _read_optional_string(
    payload: Mapping[str, Any],
    key: str,
    *,
    allowed: set[str] | None = None,
) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProjectConfigTypeError(
            f".portworld/project.json field '{key}' must be a string or null."
        )
    text = value.strip() or None
    if text is None:
        return None
    if allowed is not None and text not in allowed:
        allowed_values = ", ".join(sorted(allowed))
        raise ProjectConfigTypeError(
            f".portworld/project.json field '{key}' must be one of: {allowed_values}."
        )
    return text


def _read_bool(payload: Mapping[str, Any], key: str, *, default: bool) -> bool:
    value = payload.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ProjectConfigTypeError(
            f".portworld/project.json field '{key}' must be a boolean."
        )
    return value


def _read_int(payload: Mapping[str, Any], key: str, *, default: int) -> int:
    value = payload.get(key)
    if value is None:
        return default
    if not isinstance(value, int):
        raise ProjectConfigTypeError(
            f".portworld/project.json field '{key}' must be an integer."
        )
    return value


def _read_string_list(
    payload: Mapping[str, Any],
    key: str,
    *,
    default: tuple[str, ...],
) -> tuple[str, ...]:
    value = payload.get(key)
    if value is None:
        return default
    if not isinstance(value, list):
        raise ProjectConfigTypeError(
            f".portworld/project.json field '{key}' must be an array of strings."
        )
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ProjectConfigTypeError(
                f".portworld/project.json field '{key}' must contain only strings."
            )
        text = item.strip()
        if text:
            items.append(text)
    return tuple(items) if items else default


def _coerce_optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalized_provider(value: str | None, *, default: str) -> str:
    text = (value or "").strip().lower()
    return text or default


def _parse_bool_string(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_csv_values(
    value: str | None,
    *,
    default: tuple[str, ...],
) -> tuple[str, ...]:
    if value is None:
        return default
    items = tuple(part.strip() for part in value.split(",") if part.strip())
    return items or default


def _bool_env_value(value: bool) -> str:
    return "true" if value else "false"


def _resolve_remembered_artifact_repository(
    *,
    artifact_repository_base: str | None,
    artifact_repository: str | None,
    image_source_mode: str | None,
) -> str | None:
    if artifact_repository_base:
        return artifact_repository_base
    if artifact_repository is None:
        return None
    if (
        image_source_mode == IMAGE_SOURCE_MODE_PUBLISHED_RELEASE
        and artifact_repository.endswith(PUBLISHED_ARTIFACT_REPOSITORY_SUFFIX)
    ):
        stripped = artifact_repository[: -len(PUBLISHED_ARTIFACT_REPOSITORY_SUFFIX)].strip()
        if stripped:
            return stripped
    return artifact_repository


def _normalize_cloud_selection(
    *,
    project_mode: str,
    cloud_provider: str | None,
    preferred_target: str | None,
) -> tuple[str | None, str | None]:
    if project_mode == PROJECT_MODE_LOCAL:
        return None, None

    if preferred_target is None and cloud_provider is None:
        return CLOUD_PROVIDER_GCP, TARGET_GCP_CLOUD_RUN
    if preferred_target is None and cloud_provider is not None:
        candidate_targets = MANAGED_TARGETS_BY_PROVIDER.get(cloud_provider, ())
        if len(candidate_targets) != 1:
            raise ProjectConfigTypeError(
                "Managed cloud provider must map to one supported managed target."
            )
        return cloud_provider, candidate_targets[0]
    if preferred_target is not None and cloud_provider is None:
        return _provider_for_target(preferred_target), preferred_target

    assert preferred_target is not None and cloud_provider is not None
    expected_provider = _provider_for_target(preferred_target)
    if expected_provider != cloud_provider:
        raise ProjectConfigTypeError(
            "cloud_provider and deploy.preferred_target must refer to the same provider."
        )
    return cloud_provider, preferred_target


def _provider_for_target(target: str) -> str:
    if target == TARGET_AWS_ECS_FARGATE:
        return CLOUD_PROVIDER_AWS
    if target == TARGET_AZURE_CONTAINER_APPS:
        return CLOUD_PROVIDER_AZURE
    if target == TARGET_GCP_CLOUD_RUN:
        return CLOUD_PROVIDER_GCP
    raise ProjectConfigTypeError(f"Unsupported managed target: {target}.")
