from __future__ import annotations

from portworld_cli.workspace.project_config import (
    CLOUD_PROVIDER_GCP,
    GCP_CLOUD_RUN_TARGET,
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
    resolve_csv_value,
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
) -> SecuritySectionResult:
    validate_security_flag_conflicts(options)

    existing_env = session.existing_env
    current_profile = normalize_backend_profile(session.project_config.security.backend_profile)
    backend_profile = resolve_choice_value(
        session.cli_context,
        prompt="Backend profile",
        current_value=current_profile,
        explicit_value=normalize_backend_profile(options.backend_profile)
        if options.backend_profile is not None
        else None,
        choices=("development", "production"),
    )
    cors_origins = resolve_csv_value(
        session.cli_context,
        prompt="CORS origins (comma-separated)",
        current_values=session.project_config.security.cors_origins,
        explicit_value=options.cors_origins,
    )
    allowed_hosts = resolve_csv_value(
        session.cli_context,
        prompt="Allowed hosts (comma-separated)",
        current_values=session.project_config.security.allowed_hosts,
        explicit_value=options.allowed_hosts,
    )
    bearer_token = resolve_bearer_token(
        session.cli_context,
        existing_value="" if existing_env is None else existing_env.known_values.get("BACKEND_BEARER_TOKEN", ""),
        explicit_value=options.bearer_token,
        generate=options.generate_bearer_token,
        clear=options.clear_bearer_token,
    )
    return SecuritySectionResult(
        backend_profile=backend_profile,
        cors_origins=cors_origins,
        allowed_hosts=allowed_hosts,
        bearer_token=bearer_token,
    )


def collect_cloud_section(
    session: ConfigSession,
    options: CloudEditOptions,
    *,
    prompt_defaults_when_local: bool,
) -> CloudSectionResult:
    current_mode = session.project_config.project_mode
    current_runtime_source = session.effective_runtime_source
    project_mode = resolve_choice_value(
        session.cli_context,
        prompt="Project mode",
        current_value=current_mode,
        explicit_value=options.project_mode,
        choices=(PROJECT_MODE_LOCAL, PROJECT_MODE_MANAGED),
    )
    runtime_source = resolve_choice_value(
        session.cli_context,
        prompt="Runtime source",
        current_value=current_runtime_source,
        explicit_value=options.runtime_source,
        choices=(RUNTIME_SOURCE_SOURCE, RUNTIME_SOURCE_PUBLISHED),
    )

    current_gcp = session.project_config.deploy.gcp_cloud_run
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
        )
    )
    collect_defaults = (
        project_mode == PROJECT_MODE_MANAGED
        or prompt_defaults_when_local
        or explicit_cloud_change
    )

    gcp_cloud_run = current_gcp
    if collect_defaults:
        project_id = resolve_optional_text_value(
            session.cli_context,
            prompt="GCP project id",
            current_value=current_gcp.project_id,
            explicit_value=options.project,
        )
        region = resolve_optional_text_value(
            session.cli_context,
            prompt="Cloud Run region",
            current_value=current_gcp.region,
            explicit_value=options.region,
        )
        service_name = resolve_required_text_value(
            session.cli_context,
            prompt="Cloud Run service name",
            current_value=current_gcp.service_name,
            explicit_value=options.service,
        )
        artifact_repository = resolve_required_text_value(
            session.cli_context,
            prompt="Artifact Registry repository",
            current_value=current_gcp.artifact_repository,
            explicit_value=options.artifact_repo,
        )
        sql_instance_name = resolve_required_text_value(
            session.cli_context,
            prompt="Cloud SQL instance name",
            current_value=current_gcp.sql_instance_name,
            explicit_value=options.sql_instance,
        )
        database_name = resolve_required_text_value(
            session.cli_context,
            prompt="Cloud SQL database name",
            current_value=current_gcp.database_name,
            explicit_value=options.database,
        )
        bucket_name = resolve_optional_text_value(
            session.cli_context,
            prompt="GCS bucket name",
            current_value=current_gcp.bucket_name,
            explicit_value=options.bucket,
        )
        min_instances = resolve_int_value(
            session.cli_context,
            prompt="Minimum Cloud Run instances",
            current_value=current_gcp.min_instances,
            explicit_value=options.min_instances,
        )
        max_instances = resolve_int_value(
            session.cli_context,
            prompt="Maximum Cloud Run instances",
            current_value=current_gcp.max_instances,
            explicit_value=options.max_instances,
        )
        concurrency = resolve_int_value(
            session.cli_context,
            prompt="Cloud Run concurrency",
            current_value=current_gcp.concurrency,
            explicit_value=options.concurrency,
        )
        cpu = resolve_required_text_value(
            session.cli_context,
            prompt="Cloud Run CPU",
            current_value=current_gcp.cpu,
            explicit_value=options.cpu,
        )
        memory = resolve_required_text_value(
            session.cli_context,
            prompt="Cloud Run memory",
            current_value=current_gcp.memory,
            explicit_value=options.memory,
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

    if project_mode == PROJECT_MODE_MANAGED:
        cloud_provider = CLOUD_PROVIDER_GCP
        preferred_target = GCP_CLOUD_RUN_TARGET
    else:
        cloud_provider = None
        preferred_target = None

    return CloudSectionResult(
        project_mode=project_mode,
        runtime_source=runtime_source,
        cloud_provider=cloud_provider,
        preferred_target=preferred_target,
        gcp_cloud_run=gcp_cloud_run,
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
            cors_origins=result.cors_origins,
            allowed_hosts=result.allowed_hosts,
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
        ),
    )
    return updated_project_config, {}
