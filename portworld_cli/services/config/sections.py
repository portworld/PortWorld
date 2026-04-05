from __future__ import annotations

import re

from portworld_cli.targets import (
    CLOUD_PROVIDER_AWS,
    CLOUD_PROVIDER_AZURE,
    CLOUD_PROVIDER_GCP,
    TARGET_AWS_ECS_FARGATE,
    TARGET_AZURE_CONTAINER_APPS,
    TARGET_GCP_CLOUD_RUN,
    normalize_managed_target,
)
from portworld_cli.workspace.project_config import (
    AWSECSFargateConfig,
    AzureContainerAppsConfig,
    PROJECT_MODE_LOCAL,
    PROJECT_MODE_MANAGED,
    RUNTIME_SOURCE_PUBLISHED,
    RUNTIME_SOURCE_SOURCE,
    GCPCloudRunConfig,
    ProjectConfig,
    SecurityConfig,
)
from portworld_cli.services.config.errors import ConfigValidationError
from portworld_cli.services.config.prompts import (
    normalize_backend_profile,
    resolve_bearer_token,
    resolve_choice_value,
    resolve_int_value,
    resolve_optional_text_value,
    resolve_required_text_value,
    validate_security_flag_conflicts,
)
from portworld_cli.services.config.types import (
    CloudEditOptions,
    CloudSectionResult,
    SecurityEditOptions,
    SecuritySectionResult,
)
from portworld_cli.workspace.session import WorkspaceSession as ConfigSession


def collect_security_section(
    session: ConfigSession,
    options: SecurityEditOptions,
    *,
    quickstart: bool = False,
) -> SecuritySectionResult:
    validate_security_flag_conflicts(options)

    existing_env = session.existing_env
    current_profile = normalize_backend_profile(session.project_config.security.backend_profile)
    explicit_backend_profile = (
        normalize_backend_profile(options.backend_profile)
        if options.backend_profile is not None
        else None
    )
    if quickstart and explicit_backend_profile is None and not session.cli_context.non_interactive:
        backend_profile = current_profile
    else:
        backend_profile = resolve_choice_value(
            session.cli_context,
            prompt="Backend profile",
            current_value=current_profile,
            explicit_value=explicit_backend_profile,
            choices=("development", "production"),
        )
    existing_bearer = (
        "" if existing_env is None else existing_env.known_values.get("BACKEND_BEARER_TOKEN", "")
    )
    if (
        quickstart
        and options.bearer_token is None
        and not options.generate_bearer_token
        and not options.clear_bearer_token
        and not session.cli_context.non_interactive
    ):
        bearer_token = existing_bearer.strip()
    else:
        bearer_token = resolve_bearer_token(
            session.cli_context,
            existing_value=existing_bearer,
            explicit_value=options.bearer_token,
            generate=options.generate_bearer_token,
            clear=options.clear_bearer_token,
        )
    return SecuritySectionResult(
        backend_profile=backend_profile,
        bearer_token=bearer_token,
    )


def collect_cloud_section(
    session: ConfigSession,
    options: CloudEditOptions,
    *,
    prompt_defaults_when_local: bool,
    quickstart: bool = False,
) -> CloudSectionResult:
    current_mode = session.project_config.project_mode
    current_runtime_source = session.effective_runtime_source
    project_mode = resolve_choice_value(
        session.cli_context,
        prompt="Project mode",
        current_value=current_mode,
        explicit_value=options.project_mode,
        choices=(PROJECT_MODE_LOCAL, PROJECT_MODE_MANAGED),
        prompt_when_unspecified=not quickstart,
    )
    runtime_source = resolve_choice_value(
        session.cli_context,
        prompt="Runtime source",
        current_value=current_runtime_source,
        explicit_value=options.runtime_source,
        choices=(RUNTIME_SOURCE_SOURCE, RUNTIME_SOURCE_PUBLISHED),
        prompt_when_unspecified=not quickstart,
    )
    current_cloud_provider = session.project_config.cloud_provider or CLOUD_PROVIDER_GCP
    current_preferred_target = (
        normalize_managed_target(session.project_config.deploy.preferred_target)
        or _target_for_provider(current_cloud_provider)
    )
    cloud_provider = current_cloud_provider
    preferred_target = current_preferred_target
    if project_mode == PROJECT_MODE_MANAGED:
        cloud_provider = resolve_choice_value(
            session.cli_context,
            prompt="Cloud provider",
            current_value=current_cloud_provider,
            explicit_value=options.cloud_provider,
            choices=(CLOUD_PROVIDER_GCP, CLOUD_PROVIDER_AWS, CLOUD_PROVIDER_AZURE),
            prompt_when_unspecified=not quickstart,
        )
        preferred_target = resolve_choice_value(
            session.cli_context,
            prompt="Managed target",
            current_value=current_preferred_target,
            explicit_value=normalize_managed_target(options.target) or options.target,
            choices=(TARGET_GCP_CLOUD_RUN, TARGET_AWS_ECS_FARGATE, TARGET_AZURE_CONTAINER_APPS),
            prompt_when_unspecified=not quickstart,
        )
    default_target = _target_for_provider(cloud_provider)
    if project_mode == PROJECT_MODE_MANAGED and preferred_target != default_target:
        raise ConfigValidationError(
            "Selected managed target does not match selected cloud provider."
        )

    current_gcp = session.project_config.deploy.gcp_cloud_run
    current_aws = session.project_config.deploy.aws_ecs_fargate
    current_azure = session.project_config.deploy.azure_container_apps
    explicit_cloud_change = any(
        value is not None
        for value in (
            options.project,
            options.region,
            options.service,
            options.artifact_repo,
            options.sql_instance,
            options.database,
            options.bucket,
            options.min_instances,
            options.max_instances,
            options.concurrency,
            options.cpu,
            options.memory,
            options.aws_region,
            options.aws_service,
            options.aws_vpc_id,
            options.aws_subnet_ids,
            options.azure_subscription,
            options.azure_resource_group,
            options.azure_region,
            options.azure_environment,
            options.azure_app,
        )
    )
    collect_defaults = (
        project_mode == PROJECT_MODE_MANAGED
        or prompt_defaults_when_local
        or explicit_cloud_change
    )

    gcp_cloud_run = current_gcp
    aws_ecs_fargate = current_aws
    azure_container_apps = current_azure
    if collect_defaults and cloud_provider == CLOUD_PROVIDER_GCP:
        project_id = resolve_optional_text_value(
            session.cli_context,
            prompt="GCP project id",
            current_value=current_gcp.project_id,
            explicit_value=options.project,
            prompt_when_current_set=not quickstart,
        )
        region = resolve_optional_text_value(
            session.cli_context,
            prompt="Cloud Run region",
            current_value=current_gcp.region,
            explicit_value=options.region,
            prompt_when_current_set=not quickstart,
        )
        service_name = resolve_required_text_value(
            session.cli_context,
            prompt="Cloud Run service name",
            current_value=current_gcp.service_name,
            explicit_value=options.service,
            prompt_when_current_set=not quickstart,
        )
        artifact_repository = resolve_required_text_value(
            session.cli_context,
            prompt="Artifact Registry repository",
            current_value=current_gcp.artifact_repository,
            explicit_value=options.artifact_repo,
            prompt_when_current_set=not quickstart,
        )
        sql_instance_name = resolve_required_text_value(
            session.cli_context,
            prompt="Cloud SQL instance name",
            current_value=current_gcp.sql_instance_name,
            explicit_value=options.sql_instance,
            prompt_when_current_set=not quickstart,
        )
        database_name = resolve_required_text_value(
            session.cli_context,
            prompt="Cloud SQL database name",
            current_value=current_gcp.database_name,
            explicit_value=options.database,
            prompt_when_current_set=not quickstart,
        )
        bucket_name = resolve_optional_text_value(
            session.cli_context,
            prompt="GCS bucket name",
            current_value=current_gcp.bucket_name,
            explicit_value=options.bucket,
            prompt_when_current_set=not quickstart,
        )
        min_instances = resolve_int_value(
            session.cli_context,
            prompt="Minimum Cloud Run instances",
            current_value=current_gcp.min_instances,
            explicit_value=options.min_instances,
            prompt_when_current_set=not quickstart,
        )
        max_instances = resolve_int_value(
            session.cli_context,
            prompt="Maximum Cloud Run instances",
            current_value=current_gcp.max_instances,
            explicit_value=options.max_instances,
            prompt_when_current_set=not quickstart,
        )
        concurrency = resolve_int_value(
            session.cli_context,
            prompt="Cloud Run concurrency",
            current_value=current_gcp.concurrency,
            explicit_value=options.concurrency,
            prompt_when_current_set=not quickstart,
        )
        cpu = resolve_required_text_value(
            session.cli_context,
            prompt="Cloud Run CPU",
            current_value=current_gcp.cpu,
            explicit_value=options.cpu,
            prompt_when_current_set=not quickstart,
        )
        memory = resolve_required_text_value(
            session.cli_context,
            prompt="Cloud Run memory",
            current_value=current_gcp.memory,
            explicit_value=options.memory,
            prompt_when_current_set=not quickstart,
        )
        if min_instances < 0:
            raise ConfigValidationError("--min-instances must be >= 0.")
        if max_instances < 1:
            raise ConfigValidationError("--max-instances must be >= 1.")
        if min_instances > max_instances:
            raise ConfigValidationError("--min-instances cannot exceed --max-instances.")
        if concurrency < 1:
            raise ConfigValidationError("--concurrency must be >= 1.")
        gcp_cloud_run = GCPCloudRunConfig(
            project_id=project_id,
            region=region,
            service_name=service_name,
            artifact_repository=artifact_repository,
            sql_instance_name=sql_instance_name,
            database_name=database_name,
            bucket_name=bucket_name,
            min_instances=min_instances,
            max_instances=max_instances,
            concurrency=concurrency,
            cpu=cpu,
            memory=memory,
        )
    if collect_defaults and cloud_provider == CLOUD_PROVIDER_AWS:
        aws_subnet_ids = (
            _parse_csv_text(options.aws_subnet_ids)
            if options.aws_subnet_ids is not None
            else current_aws.subnet_ids
        )
        aws_ecs_fargate = AWSECSFargateConfig(
            region=resolve_optional_text_value(
                session.cli_context,
                prompt="AWS region",
                current_value=current_aws.region,
                explicit_value=options.aws_region,
                prompt_when_current_set=not quickstart,
            ),
            service_name=resolve_optional_text_value(
                session.cli_context,
                prompt="AWS ECS service name",
                current_value=current_aws.service_name,
                explicit_value=options.aws_service,
                prompt_when_current_set=not quickstart,
            ),
            vpc_id=(
                resolve_optional_text_value(
                    session.cli_context,
                    prompt="AWS VPC id",
                    current_value=current_aws.vpc_id,
                    explicit_value=options.aws_vpc_id,
                    prompt_when_current_set=not quickstart,
                )
                if options.aws_vpc_id is not None
                else current_aws.vpc_id
            ),
            subnet_ids=aws_subnet_ids,
        )
    if collect_defaults and cloud_provider == CLOUD_PROVIDER_AZURE:
        azure_container_apps = AzureContainerAppsConfig(
            subscription_id=resolve_optional_text_value(
                session.cli_context,
                prompt="Azure subscription id",
                current_value=current_azure.subscription_id,
                explicit_value=options.azure_subscription,
                prompt_when_current_set=not quickstart,
            ),
            resource_group=resolve_optional_text_value(
                session.cli_context,
                prompt="Azure resource group",
                current_value=current_azure.resource_group,
                explicit_value=options.azure_resource_group,
                prompt_when_current_set=not quickstart,
            ),
            region=resolve_optional_text_value(
                session.cli_context,
                prompt="Azure region",
                current_value=current_azure.region,
                explicit_value=options.azure_region,
                prompt_when_current_set=not quickstart,
            ),
            environment_name=resolve_optional_text_value(
                session.cli_context,
                prompt="Azure Container Apps environment name",
                current_value=current_azure.environment_name,
                explicit_value=options.azure_environment,
                prompt_when_current_set=not quickstart,
            ),
            app_name=resolve_optional_text_value(
                session.cli_context,
                prompt="Azure Container Apps app name",
                current_value=current_azure.app_name,
                explicit_value=options.azure_app,
                prompt_when_current_set=not quickstart,
            ),
        )

    if project_mode == PROJECT_MODE_LOCAL:
        cloud_provider = None
        preferred_target = None

    return CloudSectionResult(
        project_mode=project_mode,
        runtime_source=runtime_source,
        cloud_provider=cloud_provider,
        preferred_target=preferred_target,
        gcp_cloud_run=gcp_cloud_run,
        aws_ecs_fargate=aws_ecs_fargate,
        azure_container_apps=azure_container_apps,
    )


def apply_security_section(
    project_config: ProjectConfig,
    result: SecuritySectionResult,
) -> tuple[ProjectConfig, dict[str, str]]:
    updated_project_config = ProjectConfig(
        schema_version=project_config.schema_version,
        project_mode=project_config.project_mode,
        runtime_source=project_config.runtime_source,
        cloud_provider=project_config.cloud_provider,
        providers=project_config.providers,
        security=SecurityConfig(
            backend_profile=result.backend_profile,
        ),
        deploy=project_config.deploy,
    )
    return updated_project_config, {"BACKEND_BEARER_TOKEN": result.bearer_token}


def apply_cloud_section(
    project_config: ProjectConfig,
    result: CloudSectionResult,
) -> tuple[ProjectConfig, dict[str, str]]:
    updated_project_config = ProjectConfig(
        schema_version=project_config.schema_version,
        project_mode=result.project_mode,
        runtime_source=result.runtime_source,
        cloud_provider=result.cloud_provider,
        providers=project_config.providers,
        security=project_config.security,
        deploy=type(project_config.deploy)(
            preferred_target=result.preferred_target,
            gcp_cloud_run=result.gcp_cloud_run,
            aws_ecs_fargate=result.aws_ecs_fargate,
            azure_container_apps=result.azure_container_apps,
            published_runtime=project_config.deploy.published_runtime,
        ),
    )
    return updated_project_config, {}


def _target_for_provider(provider: str) -> str:
    if provider == CLOUD_PROVIDER_AWS:
        return TARGET_AWS_ECS_FARGATE
    if provider == CLOUD_PROVIDER_AZURE:
        return TARGET_AZURE_CONTAINER_APPS
    return TARGET_GCP_CLOUD_RUN


def _resolve_optional_csv(
    *,
    cli_context,
    prompt: str,
    current_values: tuple[str, ...],
    explicit_value: str | None,
) -> tuple[str, ...]:
    if explicit_value is not None:
        return _parse_csv_text(explicit_value)
    if cli_context.non_interactive:
        return current_values
    current_text = ",".join(current_values)
    response = resolve_optional_text_value(
        cli_context,
        prompt=prompt,
        current_value=current_text,
        explicit_value=None,
    )
    if response is None:
        return ()
    return _parse_csv_text(response)


def _parse_csv_text(raw: str) -> tuple[str, ...]:
    values = tuple(
        value
        for value in (
            re.sub(r"\s+", "", part.strip())
            for part in raw.split(",")
            if part.strip()
        )
        if value
    )
    return values
