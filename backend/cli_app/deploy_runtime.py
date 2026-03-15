from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import os
import re
import secrets
import subprocess
from time import time_ns
from typing import Any, Iterator

import click
import httpx

from backend.cli_app.context import CLIContext
from backend.cli_app.deploy_state import DeployState, read_deploy_state, write_deploy_state
from backend.cli_app.envfile import EnvFileParseError, load_env_template, parse_env_file
from backend.cli_app.gcp import (
    GCPAdapters,
    REQUIRED_GCP_SERVICES,
    build_image_uri,
    build_postgres_url,
    build_service_account_email,
    resolve_project_id,
    resolve_region,
)
from backend.cli_app.output import CommandResult, DiagnosticCheck, format_key_value_lines
from backend.cli_app.paths import ProjectRootResolutionError
from backend.cli_app.project_config import (
    DEFAULT_GCP_ARTIFACT_REPOSITORY,
    DEFAULT_GCP_CONCURRENCY,
    DEFAULT_GCP_CPU,
    DEFAULT_GCP_DATABASE_NAME,
    DEFAULT_GCP_MAX_INSTANCES,
    DEFAULT_GCP_MEMORY,
    DEFAULT_GCP_MIN_INSTANCES,
    DEFAULT_GCP_REGION,
    DEFAULT_GCP_SERVICE_NAME,
    DEFAULT_GCP_SQL_INSTANCE_NAME,
    ProjectConfig,
    ProjectConfigError,
    load_project_config,
)
from backend.cli_app.state import CLIStateDecodeError, CLIStateTypeError
from backend.core.settings import Settings


COMMAND_NAME = "portworld deploy gcp-cloud-run"
DEFAULT_REGION = DEFAULT_GCP_REGION
DEFAULT_SERVICE_NAME = DEFAULT_GCP_SERVICE_NAME
DEFAULT_ARTIFACT_REPOSITORY = DEFAULT_GCP_ARTIFACT_REPOSITORY
DEFAULT_SQL_INSTANCE_NAME = DEFAULT_GCP_SQL_INSTANCE_NAME
DEFAULT_DATABASE_NAME = DEFAULT_GCP_DATABASE_NAME
DEFAULT_TIMEOUT = "3600s"
DEFAULT_CPU = DEFAULT_GCP_CPU
DEFAULT_MEMORY = DEFAULT_GCP_MEMORY
DEFAULT_MIN_INSTANCES = DEFAULT_GCP_MIN_INSTANCES
DEFAULT_MAX_INSTANCES = DEFAULT_GCP_MAX_INSTANCES
DEFAULT_CONCURRENCY = DEFAULT_GCP_CONCURRENCY
DEFAULT_ALLOWED_HOSTS = "*.a.run.app"
DEFAULT_SQL_DATABASE_VERSION = "POSTGRES_16"
DEFAULT_SQL_CPU_COUNT = 1
DEFAULT_SQL_MEMORY = "3840MiB"
DEFAULT_SQL_USER_NAME = "portworld_app"
DEFAULT_BUCKET_SUFFIX = "portworld-artifacts"
IMAGE_NAME = "portworld-backend"
INGRESS_SETTING = "all"

SENSITIVE_ENV_KEYS: tuple[str, ...] = (
    "OPENAI_API_KEY",
    "VISION_PROVIDER_API_KEY",
    "TAVILY_API_KEY",
    "BACKEND_BEARER_TOKEN",
    "BACKEND_DATABASE_URL",
)
LOCAL_ONLY_ENV_KEYS: tuple[str, ...] = (
    "BACKEND_DATA_DIR",
    "BACKEND_SQLITE_PATH",
    "PORT",
)


class DeployUsageError(RuntimeError):
    pass


class DeployStageError(RuntimeError):
    def __init__(self, *, stage: str, message: str, action: str | None = None) -> None:
        super().__init__(message)
        self.stage = stage
        self.action = action


@dataclass(frozen=True, slots=True)
class DeployGCPCloudRunOptions:
    project: str | None
    region: str | None
    service: str | None
    artifact_repo: str | None
    sql_instance: str | None
    database: str | None
    bucket: str | None
    cors_origins: str | None
    allowed_hosts: str | None
    tag: str | None
    min_instances: int | None
    max_instances: int | None
    concurrency: int | None
    cpu: str | None
    memory: str | None


@dataclass(frozen=True, slots=True)
class ResolvedDeployConfig:
    project_id: str
    region: str
    service_name: str
    artifact_repository: str
    sql_instance_name: str
    database_name: str
    bucket_name: str
    cors_origins: str
    allowed_hosts: str
    image_tag: str
    min_instances: int
    max_instances: int
    concurrency: int
    cpu: str
    memory: str


def run_deploy_gcp_cloud_run(
    cli_context: CLIContext,
    options: DeployGCPCloudRunOptions,
) -> CommandResult:
    stage_records: list[dict[str, object]] = []
    resources: dict[str, object] = {}
    checks: list[DiagnosticCheck] = []

    try:
        project_paths = cli_context.resolve_project_paths()
        if not project_paths.env_file.is_file():
            raise DeployStageError(
                stage="repo_config_discovery",
                message="backend/.env is missing.",
                action="Run `portworld init` first.",
            )

        template = load_env_template(project_paths.env_example_file)
        parsed_env = parse_env_file(project_paths.env_file, template=template)
        env_values = template.defaults()
        env_values.update(parsed_env.known_values)
        project_config = load_project_config(project_paths.project_config_file)
        remembered_state = read_deploy_state(project_paths.gcp_cloud_run_state_file)
        _record_stage(
            stage_records,
            stage="repo_config_discovery",
            message="Resolved repo root and loaded CLI config inputs.",
            details={
                "project_root": str(project_paths.project_root),
                "env_file": str(project_paths.env_file),
                "project_config_file": str(project_paths.project_config_file),
                "state_file": str(project_paths.gcp_cloud_run_state_file),
            },
        )

        adapters = GCPAdapters.create()
        active_account = _require_active_gcloud_account(adapters=adapters)
        _record_stage(
            stage_records,
            stage="prerequisite_validation",
            message="Validated gcloud installation and authentication.",
            details={"account": active_account},
        )

        config = _resolve_deploy_config(
            cli_context,
            adapters=adapters,
            env_values=env_values,
            project_config=project_config,
            remembered_state=remembered_state,
            options=options,
            project_root=project_paths.project_root,
        )
        resources.update(
            {
                "project_id": config.project_id,
                "region": config.region,
                "artifact_registry_repository": config.artifact_repository,
                "cloud_sql_instance": config.sql_instance_name,
                "database_name": config.database_name,
                "bucket_name": config.bucket_name,
            }
        )
        _record_stage(
            stage_records,
            stage="parameter_resolution",
            message="Resolved deploy parameters and production posture overrides.",
            details={
                "project_id": config.project_id,
                "region": config.region,
                "service_name": config.service_name,
                "artifact_repository": config.artifact_repository,
                "sql_instance_name": config.sql_instance_name,
                "database_name": config.database_name,
                "bucket_name": config.bucket_name,
                "cors_origins": config.cors_origins,
                "allowed_hosts": config.allowed_hosts,
                "image_tag": config.image_tag,
            },
        )

        _confirm_mutations(cli_context, config=config)

        api_statuses = _ensure_required_apis(adapters=adapters, config=config)
        _record_stage(
            stage_records,
            stage="api_enablement",
            message="Verified required GCP APIs are enabled.",
            details={"required_apis": [status.service_name for status in api_statuses]},
        )

        service_account_email = _ensure_runtime_service_account(
            adapters=adapters,
            config=config,
        )
        resources["service_account"] = service_account_email
        _record_stage(
            stage_records,
            stage="service_account_setup",
            message="Ensured runtime service account and project IAM bindings.",
            details={"service_account_email": service_account_email},
        )

        repository_ref = _ensure_artifact_repository(adapters=adapters, config=config)
        resources["artifact_registry_repository"] = repository_ref.repository
        _record_stage(
            stage_records,
            stage="artifact_registry_setup",
            message="Ensured Artifact Registry repository exists.",
            details={"repository": repository_ref.repository},
        )

        image_uri = build_image_uri(
            project_id=config.project_id,
            region=config.region,
            repository=config.artifact_repository,
            image_name=IMAGE_NAME,
            tag=config.image_tag,
        )
        build_submission = _submit_cloud_build(
            adapters=adapters,
            project_root=project_paths.project_root,
            dockerfile_path=project_paths.dockerfile,
            project_id=config.project_id,
            image_uri=image_uri,
        )
        resources["image"] = image_uri
        _record_stage(
            stage_records,
            stage="cloud_build",
            message="Built and published the backend image.",
            details={
                "image": image_uri,
                "build_id": build_submission.build_id,
                "log_url": build_submission.log_url,
            },
        )

        non_db_secret_names, openai_secret_name, vision_secret_name, tavily_secret_name, bearer_secret_name, bearer_token_for_validation = _ensure_core_secrets(
            adapters=adapters,
            config=config,
            env_values=env_values,
        )
        _record_stage(
            stage_records,
            stage="secret_manager_setup",
            message="Ensured non-database runtime secrets exist.",
            details={"secrets": non_db_secret_names},
        )

        sql_instance_ref, database_url_secret_name, database_url_for_validation = _ensure_cloud_sql(
            adapters=adapters,
            config=config,
        )
        resources["cloud_sql_instance"] = sql_instance_ref.instance_name
        _record_stage(
            stage_records,
            stage="cloud_sql_setup",
            message="Ensured Cloud SQL instance, database, user, and database URL secret.",
            details={
                "instance_name": sql_instance_ref.instance_name,
                "connection_name": sql_instance_ref.connection_name,
                "database_url_secret_name": database_url_secret_name,
            },
        )

        bucket_name = _ensure_gcs_bucket(adapters=adapters, cli_context=cli_context, config=config)
        resources["bucket_name"] = bucket_name
        _ensure_bucket_binding(
            adapters=adapters,
            bucket_name=bucket_name,
            service_account_email=service_account_email,
        )
        _record_stage(
            stage_records,
            stage="gcs_bucket_setup",
            message="Ensured artifact bucket and bucket IAM binding.",
            details={"bucket_name": bucket_name},
        )

        env_vars = _build_runtime_env_vars(
            env_values=env_values,
            config=config,
            bucket_name=bucket_name,
        )
        secret_bindings = _build_cloud_run_secret_bindings(
            openai_secret_name=openai_secret_name,
            vision_secret_name=vision_secret_name,
            tavily_secret_name=tavily_secret_name,
            bearer_secret_name=bearer_secret_name,
            database_url_secret_name=database_url_secret_name,
        )
        _validate_final_settings(
            env_vars=env_vars,
            env_values=env_values,
            secret_placeholders={
                "BACKEND_BEARER_TOKEN": bearer_token_for_validation,
                "BACKEND_DATABASE_URL": database_url_for_validation,
            },
        )
        _record_stage(
            stage_records,
            stage="runtime_config_assembly",
            message="Assembled managed Cloud Run runtime configuration.",
            details={
                "env_var_count": len(env_vars),
                "secret_binding_count": len(secret_bindings),
            },
        )

        deploy_outcome = _deploy_cloud_run_service(
            adapters=adapters,
            config=config,
            image_uri=image_uri,
            service_account_email=service_account_email,
            env_vars=env_vars,
            secret_bindings=secret_bindings,
            sql_instance_ref=sql_instance_ref,
        )
        service_ref = deploy_outcome.resource
        resources["service_name"] = service_ref.service_name
        resources["service_url"] = service_ref.url
        _record_stage(
            stage_records,
            stage="cloud_run_deploy",
            message="Deployed the Cloud Run service.",
            details={
                "action": deploy_outcome.action,
                "service_name": service_ref.service_name,
                "service_url": service_ref.url,
                "image": service_ref.image,
            },
        )

        liveness_probe_ok = True
        if service_ref.url:
            liveness_probe_ok = _probe_liveness(service_ref.url)
            if not liveness_probe_ok:
                checks.append(
                    DiagnosticCheck(
                        id="liveness_probe",
                        status="warn",
                        message="Cloud Run service deployed, but the final /healthz probe did not succeed.",
                        action="Wait for the revision to finish starting, then re-run the health check command from the summary.",
                    )
                )
        _record_stage(
            stage_records,
            stage="post_deploy_validation",
            message="Collected final deploy summary and follow-up commands.",
            details={
                "service_url": service_ref.url,
                "liveness_probe_ok": liveness_probe_ok,
            },
        )

        write_deploy_state(
            project_paths.gcp_cloud_run_state_file,
            DeployState(
                project_id=config.project_id,
                region=config.region,
                service_name=config.service_name,
                artifact_repository=config.artifact_repository,
                cloud_sql_instance=config.sql_instance_name,
                database_name=config.database_name,
                bucket_name=bucket_name,
                image=image_uri,
                service_url=service_ref.url,
                service_account_email=service_account_email,
                last_deployed_at_ms=_now_ms(),
            ),
        )

        features = _build_feature_summary(env_values)
        next_steps = _build_next_steps(
            service_url=service_ref.url,
            project_id=config.project_id,
            region=config.region,
            bearer_secret_name=bearer_secret_name,
        )
        message = _build_success_message(
            config=config,
            service_url=service_ref.url,
            image_uri=image_uri,
            service_account_email=service_account_email,
            bucket_name=bucket_name,
            features=features,
            next_steps=next_steps,
        )
        return CommandResult(
            ok=True,
            command=COMMAND_NAME,
            message=message,
            data={
                "project_id": config.project_id,
                "region": config.region,
                "service_name": config.service_name,
                "service_url": service_ref.url,
                "image": image_uri,
                "resources": {
                    "artifact_registry_repository": config.artifact_repository,
                    "cloud_sql_instance": config.sql_instance_name,
                    "database_name": config.database_name,
                    "bucket_name": bucket_name,
                    "service_account": service_account_email,
                },
                "features": features,
                "next_steps": next_steps,
                "stages": stage_records,
            },
            checks=tuple(checks),
            exit_code=0,
        )
    except ProjectRootResolutionError as exc:
        return _failure_result(
            stage="repo_config_discovery",
            exc=exc,
            stage_records=stage_records,
            resources=resources,
            action="Run from a PortWorld repo checkout or pass --project-root.",
            error_type=type(exc).__name__,
        )
    except (
        EnvFileParseError,
        CLIStateDecodeError,
        CLIStateTypeError,
        DeployUsageError,
        ProjectConfigError,
    ) as exc:
        return _failure_result(
            stage="parameter_resolution",
            exc=exc,
            stage_records=stage_records,
            resources=resources,
            action=None,
            error_type=type(exc).__name__,
            exit_code=2,
        )
    except DeployStageError as exc:
        return _failure_result(
            stage=exc.stage,
            exc=exc,
            stage_records=stage_records,
            resources=resources,
            action=exc.action,
            error_type=type(exc).__name__,
        )
    except click.Abort:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message="Aborted before deploy completed.",
            data={
                "stage": "parameter_resolution",
                "error_type": "Abort",
                "stages": stage_records,
                "resources": resources,
            },
            exit_code=1,
        )
    except Exception as exc:
        return _failure_result(
            stage="deploy",
            exc=exc,
            stage_records=stage_records,
            resources=resources,
            action=None,
            error_type=type(exc).__name__,
        )


def _resolve_deploy_config(
    cli_context: CLIContext,
    *,
    adapters: GCPAdapters,
    env_values: OrderedDict[str, str],
    project_config: ProjectConfig | None,
    remembered_state: DeployState,
    options: DeployGCPCloudRunOptions,
    project_root: Path,
) -> ResolvedDeployConfig:
    gcp_defaults = None if project_config is None else project_config.deploy.gcp_cloud_run
    configured_project = adapters.auth.get_configured_project()
    project_id = resolve_project_id(
        explicit_project_id=options.project,
        project_config_project_id=(
            None if gcp_defaults is None else gcp_defaults.project_id
        ),
        configured_project_id=configured_project.value if configured_project.ok else None,
        remembered_project_id=remembered_state.project_id,
        allow_remembered=True,
    ).value
    project_id = _prompt_or_require_text(
        cli_context,
        prompt="GCP project id",
        value=project_id,
        error_message="A GCP project id is required. Pass --project or set a gcloud default project.",
    )

    configured_region = adapters.auth.get_configured_run_region()
    region = resolve_region(
        explicit_region=options.region,
        project_config_region=None if gcp_defaults is None else gcp_defaults.region,
        configured_region=configured_region.value if configured_region.ok else None,
        remembered_region=remembered_state.region,
        allow_remembered=True,
        default_region=DEFAULT_REGION,
    ).value
    region = _prompt_or_require_text(
        cli_context,
        prompt="Cloud Run region",
        value=region,
        error_message="A Cloud Run region is required.",
        default=DEFAULT_REGION,
    )

    service_name = _resolve_text_value(
        explicit=options.service,
        remembered=_first_non_empty(
            None if gcp_defaults is None else gcp_defaults.service_name,
            remembered_state.service_name,
        ),
        default=DEFAULT_SERVICE_NAME,
    )
    artifact_repository = _resolve_text_value(
        explicit=options.artifact_repo,
        remembered=_first_non_empty(
            None if gcp_defaults is None else gcp_defaults.artifact_repository,
            remembered_state.artifact_repository,
        ),
        default=DEFAULT_ARTIFACT_REPOSITORY,
    )
    sql_instance_name = _resolve_text_value(
        explicit=options.sql_instance,
        remembered=_first_non_empty(
            None if gcp_defaults is None else gcp_defaults.sql_instance_name,
            remembered_state.cloud_sql_instance,
        ),
        default=DEFAULT_SQL_INSTANCE_NAME,
    )
    database_name = _resolve_text_value(
        explicit=options.database,
        remembered=_first_non_empty(
            None if gcp_defaults is None else gcp_defaults.database_name,
            remembered_state.database_name,
        ),
        default=DEFAULT_DATABASE_NAME,
    )
    bucket_name = _resolve_text_value(
        explicit=options.bucket,
        remembered=_first_non_empty(
            None if gcp_defaults is None else gcp_defaults.bucket_name,
            remembered_state.bucket_name,
        ),
        default=_default_bucket_name(project_id),
    )

    existing_cors = _explicit_csv_or_none(env_values.get("CORS_ORIGINS"))
    configured_cors = (
        None
        if project_config is None
        else _csv_or_none(project_config.security.cors_origins)
    )
    cors_origins = _prompt_or_require_text(
        cli_context,
        prompt="Production CORS origins (comma-separated)",
        value=_first_non_empty(options.cors_origins, configured_cors, existing_cors),
        error_message="Explicit production CORS origins are required for deploy.",
    )
    if cors_origins.strip() == "*":
        raise DeployUsageError("CORS_ORIGINS cannot be '*' for production deploy.")

    existing_allowed_hosts = _explicit_csv_or_none(env_values.get("BACKEND_ALLOWED_HOSTS"))
    configured_allowed_hosts = (
        None
        if project_config is None
        else _csv_or_none(project_config.security.allowed_hosts)
    )
    allowed_hosts = _first_non_empty(
        options.allowed_hosts,
        configured_allowed_hosts,
        existing_allowed_hosts,
        DEFAULT_ALLOWED_HOSTS,
    )
    if allowed_hosts is None:
        raise DeployUsageError("Explicit allowed hosts could not be resolved for deploy.")
    if allowed_hosts.strip() == "*":
        raise DeployUsageError("BACKEND_ALLOWED_HOSTS cannot be '*' for production deploy.")

    image_tag = _resolve_text_value(
        explicit=options.tag,
        remembered=None,
        default=_default_image_tag(project_root=project_root),
    )
    min_instances = (
        options.min_instances
        if options.min_instances is not None
        else (
            gcp_defaults.min_instances
            if gcp_defaults is not None
            else DEFAULT_MIN_INSTANCES
        )
    )
    max_instances = (
        options.max_instances
        if options.max_instances is not None
        else (
            gcp_defaults.max_instances
            if gcp_defaults is not None
            else DEFAULT_MAX_INSTANCES
        )
    )
    concurrency = (
        options.concurrency
        if options.concurrency is not None
        else (
            gcp_defaults.concurrency
            if gcp_defaults is not None
            else DEFAULT_CONCURRENCY
        )
    )
    cpu = _resolve_text_value(
        explicit=options.cpu,
        remembered=None if gcp_defaults is None else gcp_defaults.cpu,
        default=DEFAULT_CPU,
    )
    memory = _resolve_text_value(
        explicit=options.memory,
        remembered=None if gcp_defaults is None else gcp_defaults.memory,
        default=DEFAULT_MEMORY,
    )

    if min_instances < 0:
        raise DeployUsageError("--min-instances must be >= 0.")
    if max_instances < 1:
        raise DeployUsageError("--max-instances must be >= 1.")
    if min_instances > max_instances:
        raise DeployUsageError("--min-instances cannot exceed --max-instances.")
    if concurrency < 1:
        raise DeployUsageError("--concurrency must be >= 1.")

    return ResolvedDeployConfig(
        project_id=project_id,
        region=region,
        service_name=service_name,
        artifact_repository=artifact_repository,
        sql_instance_name=sql_instance_name,
        database_name=database_name,
        bucket_name=bucket_name,
        cors_origins=cors_origins,
        allowed_hosts=allowed_hosts,
        image_tag=image_tag,
        min_instances=min_instances,
        max_instances=max_instances,
        concurrency=concurrency,
        cpu=cpu,
        memory=memory,
    )


def _require_active_gcloud_account(*, adapters: GCPAdapters) -> str:
    probe = adapters.auth.probe_gcloud()
    if not probe.ok:
        raise DeployStageError(
            stage="prerequisite_validation",
            message=_gcp_error_message(probe.error, "gcloud is not available."),
            action=_gcp_error_action(
                probe.error,
                "Install the Google Cloud SDK and make `gcloud` available on PATH.",
            ),
        )
    account_result = adapters.auth.get_active_account()
    if not account_result.ok:
        raise DeployStageError(
            stage="prerequisite_validation",
            message=_gcp_error_message(
                account_result.error,
                "Unable to determine the active gcloud account.",
            ),
            action=_gcp_error_action(
                account_result.error,
                "Run `gcloud auth login` and select the intended account.",
            ),
        )
    if account_result.value is None:
        raise DeployStageError(
            stage="prerequisite_validation",
            message="No active gcloud account is configured.",
            action="Run `gcloud auth login` and select the intended account.",
        )
    return account_result.value.account


def _confirm_mutations(cli_context: CLIContext, *, config: ResolvedDeployConfig) -> None:
    if cli_context.non_interactive or cli_context.yes:
        return
    message = "\n".join(
        [
            "Proceed with Cloud Run deploy and GCP provisioning?",
            f"project: {config.project_id}",
            f"region: {config.region}",
            f"service: {config.service_name}",
            f"artifact_repo: {config.artifact_repository}",
            f"sql_instance: {config.sql_instance_name}",
            f"bucket: {config.bucket_name}",
        ]
    )
    confirmed = click.confirm(message, default=True, show_default=True)
    if not confirmed:
        raise click.Abort()


def _ensure_required_apis(*, adapters: GCPAdapters, config: ResolvedDeployConfig):
    statuses_result = adapters.service_usage.get_api_statuses(
        project_id=config.project_id,
        service_names=REQUIRED_GCP_SERVICES,
    )
    if not statuses_result.ok:
        raise DeployStageError(
            stage="api_enablement",
            message=_gcp_error_message(statuses_result.error, "Unable to inspect required GCP APIs."),
            action=_gcp_error_action(statuses_result.error, "Verify project access and retry."),
        )
    statuses = statuses_result.value or ()
    missing = [status.service_name for status in statuses if not status.enabled]
    if missing:
        enable_result = adapters.service_usage.enable_apis(
            project_id=config.project_id,
            service_names=tuple(missing),
        )
        if not enable_result.ok:
            raise DeployStageError(
                stage="api_enablement",
                message=_gcp_error_message(enable_result.error, "Failed enabling required GCP APIs."),
                action=_gcp_error_action(enable_result.error, "Enable the listed APIs and rerun deploy."),
            )
        return enable_result.value.resource
    return statuses


def _ensure_runtime_service_account(*, adapters: GCPAdapters, config: ResolvedDeployConfig) -> str:
    account_id = _runtime_service_account_id(config.service_name)
    service_account_result = adapters.iam.create_service_account(
        project_id=config.project_id,
        account_id=account_id,
        display_name=f"{config.service_name} runtime",
    )
    if not service_account_result.ok:
        raise DeployStageError(
            stage="service_account_setup",
            message=_gcp_error_message(service_account_result.error, "Failed creating runtime service account."),
            action=_gcp_error_action(service_account_result.error, "Verify IAM permissions and rerun deploy."),
        )
    service_account_email = build_service_account_email(
        account_id=account_id,
        project_id=config.project_id,
    )
    for role in ("roles/secretmanager.secretAccessor", "roles/cloudsql.client"):
        bind_result = adapters.iam.bind_project_role(
            project_id=config.project_id,
            service_account_email=service_account_email,
            role=role,
        )
        if not bind_result.ok:
            raise DeployStageError(
                stage="service_account_setup",
                message=_gcp_error_message(bind_result.error, f"Failed binding {role} to runtime service account."),
                action=_gcp_error_action(bind_result.error, "Verify IAM permissions and rerun deploy."),
            )
    return service_account_email


def _ensure_artifact_repository(*, adapters: GCPAdapters, config: ResolvedDeployConfig):
    result = adapters.artifact_registry.create_repository(
        project_id=config.project_id,
        region=config.region,
        repository=config.artifact_repository,
        description="PortWorld backend images",
    )
    if not result.ok:
        raise DeployStageError(
            stage="artifact_registry_setup",
            message=_gcp_error_message(result.error, "Failed creating Artifact Registry repository."),
            action=_gcp_error_action(result.error, "Verify Artifact Registry permissions and retry."),
        )
    return result.value.resource


def _submit_cloud_build(
    *,
    adapters: GCPAdapters,
    project_root: Path,
    dockerfile_path: Path,
    project_id: str,
    image_uri: str,
):
    result = adapters.cloud_build.submit_build(
        project_id=project_id,
        source_dir=project_root,
        dockerfile_path=dockerfile_path,
        image_uri=image_uri,
    )
    if not result.ok:
        raise DeployStageError(
            stage="cloud_build",
            message=_gcp_error_message(result.error, "Cloud Build submission failed."),
            action=_gcp_error_action(result.error, "Inspect the Cloud Build error output and rerun deploy."),
        )
    assert result.value is not None
    return result.value


def _ensure_core_secrets(
    *,
    adapters: GCPAdapters,
    config: ResolvedDeployConfig,
    env_values: OrderedDict[str, str],
) -> tuple[list[str], str, str | None, str | None, str, str]:
    created_names: list[str] = []

    openai_secret_name = _ensure_secret_version(
        adapters=adapters,
        project_id=config.project_id,
        secret_name=_service_secret_name(config.service_name, "openai-api-key"),
        secret_value=_required_env_value(env_values, "OPENAI_API_KEY"),
        stage="secret_manager_setup",
    )
    created_names.append(openai_secret_name)

    vision_secret_name = None
    if _parse_bool_string(env_values.get("VISION_MEMORY_ENABLED", "false")):
        vision_secret_name = _ensure_secret_version(
            adapters=adapters,
            project_id=config.project_id,
            secret_name=_service_secret_name(config.service_name, "vision-provider-api-key"),
            secret_value=_required_env_value(env_values, "VISION_PROVIDER_API_KEY"),
            stage="secret_manager_setup",
        )
        created_names.append(vision_secret_name)

    tavily_secret_name = None
    tooling_enabled = _parse_bool_string(env_values.get("REALTIME_TOOLING_ENABLED", "false"))
    web_search_provider = (env_values.get("REALTIME_WEB_SEARCH_PROVIDER", "") or "").strip().lower()
    if tooling_enabled and web_search_provider == "tavily":
        tavily_secret_name = _ensure_secret_version(
            adapters=adapters,
            project_id=config.project_id,
            secret_name=_service_secret_name(config.service_name, "tavily-api-key"),
            secret_value=_required_env_value(env_values, "TAVILY_API_KEY"),
            stage="secret_manager_setup",
        )
        created_names.append(tavily_secret_name)

    bearer_secret_name = _service_secret_name(config.service_name, "backend-bearer-token")
    bearer_secret_result = adapters.secret_manager.get_secret(
        project_id=config.project_id,
        secret_name=bearer_secret_name,
    )
    if not bearer_secret_result.ok:
        raise DeployStageError(
            stage="secret_manager_setup",
            message=_gcp_error_message(bearer_secret_result.error, "Unable to inspect bearer-token secret."),
            action=_gcp_error_action(bearer_secret_result.error, "Verify Secret Manager access and rerun deploy."),
        )
    bearer_token = (env_values.get("BACKEND_BEARER_TOKEN", "") or "").strip()
    if bearer_secret_result.value is None:
        _ensure_secret_exists(
            adapters=adapters,
            project_id=config.project_id,
            secret_name=bearer_secret_name,
            stage="secret_manager_setup",
        )
        if not bearer_token:
            bearer_token = _generate_secure_token()
        _add_secret_version(
            adapters=adapters,
            project_id=config.project_id,
            secret_name=bearer_secret_name,
            secret_value=bearer_token,
            stage="secret_manager_setup",
        )
    elif bearer_token:
        _add_secret_version(
            adapters=adapters,
            project_id=config.project_id,
            secret_name=bearer_secret_name,
            secret_value=bearer_token,
            stage="secret_manager_setup",
        )
    created_names.append(bearer_secret_name)

    return (
        created_names,
        openai_secret_name,
        vision_secret_name,
        tavily_secret_name,
        bearer_secret_name,
        bearer_token or "__SECRET__",
    )


def _ensure_cloud_sql(
    *,
    adapters: GCPAdapters,
    config: ResolvedDeployConfig,
) -> tuple[Any, str, str]:
    instance_result = adapters.cloud_sql.create_instance(
        project_id=config.project_id,
        region=config.region,
        instance_name=config.sql_instance_name,
        database_version=DEFAULT_SQL_DATABASE_VERSION,
        cpu_count=DEFAULT_SQL_CPU_COUNT,
        memory=DEFAULT_SQL_MEMORY,
    )
    if not instance_result.ok:
        raise DeployStageError(
            stage="cloud_sql_setup",
            message=_gcp_error_message(instance_result.error, "Failed creating Cloud SQL instance."),
            action=_gcp_error_action(instance_result.error, "Verify Cloud SQL Admin permissions and retry."),
        )
    instance_ref = instance_result.value.resource

    database_result = adapters.cloud_sql.create_database(
        project_id=config.project_id,
        instance_name=config.sql_instance_name,
        database_name=config.database_name,
    )
    if not database_result.ok:
        raise DeployStageError(
            stage="cloud_sql_setup",
            message=_gcp_error_message(database_result.error, "Failed creating Cloud SQL database."),
            action=_gcp_error_action(database_result.error, "Verify Cloud SQL permissions and retry."),
        )

    db_password = _generate_secure_token(length=24)
    user_result = adapters.cloud_sql.create_or_update_user(
        project_id=config.project_id,
        instance_name=config.sql_instance_name,
        user_name=DEFAULT_SQL_USER_NAME,
        password=db_password,
    )
    if not user_result.ok:
        raise DeployStageError(
            stage="cloud_sql_setup",
            message=_gcp_error_message(user_result.error, "Failed creating or updating the Cloud SQL application user."),
            action=_gcp_error_action(user_result.error, "Verify Cloud SQL permissions and retry."),
        )

    if not instance_ref.connection_name or not instance_ref.primary_ip_address:
        refreshed = adapters.cloud_sql.get_instance(
            project_id=config.project_id,
            instance_name=config.sql_instance_name,
        )
        if not refreshed.ok:
            raise DeployStageError(
                stage="cloud_sql_setup",
                message=_gcp_error_message(refreshed.error, "Failed refreshing Cloud SQL instance details."),
                action=_gcp_error_action(refreshed.error, "Wait for the instance to finish provisioning and rerun deploy."),
            )
        if refreshed.value is not None:
            instance_ref = refreshed.value

    if instance_ref.connection_name:
        database_url = build_postgres_url(
            username=DEFAULT_SQL_USER_NAME,
            password=db_password,
            database_name=config.database_name,
            unix_socket_path=f"/cloudsql/{instance_ref.connection_name}",
        )
    elif instance_ref.primary_ip_address:
        database_url = build_postgres_url(
            username=DEFAULT_SQL_USER_NAME,
            password=db_password,
            database_name=config.database_name,
            host=instance_ref.primary_ip_address,
        )
    else:
        raise DeployStageError(
            stage="cloud_sql_setup",
            message="Cloud SQL instance does not expose a connection name or primary IP address yet.",
            action="Wait for the instance to finish provisioning, then rerun deploy.",
        )
    database_url_secret_name = _service_secret_name(config.service_name, "backend-database-url")
    _ensure_secret_version(
        adapters=adapters,
        project_id=config.project_id,
        secret_name=database_url_secret_name,
        secret_value=database_url,
        stage="cloud_sql_setup",
    )
    return instance_ref, database_url_secret_name, database_url


def _ensure_gcs_bucket(
    *,
    adapters: GCPAdapters,
    cli_context: CLIContext,
    config: ResolvedDeployConfig,
) -> str:
    bucket_name = config.bucket_name
    while True:
        result = adapters.gcs.create_bucket(
            project_id=config.project_id,
            bucket_name=bucket_name,
            location=config.region,
        )
        if result.ok:
            return result.value.resource.name
        error = result.error
        if (
            cli_context.non_interactive
            or cli_context.yes
            or bucket_name != config.bucket_name
            or error is None
            or error.code not in {"already_exists", "permission_denied"}
        ):
            raise DeployStageError(
                stage="gcs_bucket_setup",
                message=_gcp_error_message(error, "Failed creating or reusing the artifact bucket."),
                action=_gcp_error_action(
                    error,
                    "Provide --bucket with an alternative globally unique bucket name and retry.",
                ),
            )
        bucket_name = click.prompt(
            "Default bucket name is unavailable. Enter an alternative GCS bucket name",
            type=str,
        ).strip()
        if not bucket_name:
            raise click.Abort()


def _ensure_bucket_binding(
    *,
    adapters: GCPAdapters,
    bucket_name: str,
    service_account_email: str,
) -> None:
    result = adapters.iam.bind_bucket_role(
        bucket_name=bucket_name,
        service_account_email=service_account_email,
        role="roles/storage.objectAdmin",
    )
    if not result.ok:
        raise DeployStageError(
            stage="gcs_bucket_setup",
            message=_gcp_error_message(result.error, "Failed binding bucket IAM role for the runtime service account."),
            action=_gcp_error_action(result.error, "Verify bucket permissions and rerun deploy."),
        )


def _build_runtime_env_vars(
    *,
    env_values: OrderedDict[str, str],
    config: ResolvedDeployConfig,
    bucket_name: str,
) -> dict[str, str]:
    final_env: OrderedDict[str, str] = OrderedDict()
    for key, value in env_values.items():
        if key in SENSITIVE_ENV_KEYS or key in LOCAL_ONLY_ENV_KEYS:
            continue
        final_env[key] = value

    final_env["BACKEND_PROFILE"] = "production"
    final_env["BACKEND_STORAGE_BACKEND"] = "postgres_gcs"
    final_env["BACKEND_OBJECT_STORE_PROVIDER"] = "gcs"
    final_env["BACKEND_OBJECT_STORE_BUCKET"] = bucket_name
    final_env["BACKEND_OBJECT_STORE_PREFIX"] = config.service_name
    final_env["CORS_ORIGINS"] = config.cors_origins
    final_env["BACKEND_ALLOWED_HOSTS"] = config.allowed_hosts
    final_env["BACKEND_DEBUG_TRACE_WS_MESSAGES"] = "false"
    return dict(final_env)


def _build_cloud_run_secret_bindings(
    *,
    openai_secret_name: str,
    vision_secret_name: str | None,
    tavily_secret_name: str | None,
    bearer_secret_name: str,
    database_url_secret_name: str,
) -> dict[str, str]:
    bindings = {
        "OPENAI_API_KEY": f"{openai_secret_name}:latest",
        "BACKEND_BEARER_TOKEN": f"{bearer_secret_name}:latest",
        "BACKEND_DATABASE_URL": f"{database_url_secret_name}:latest",
    }
    if vision_secret_name is not None:
        bindings["VISION_PROVIDER_API_KEY"] = f"{vision_secret_name}:latest"
    if tavily_secret_name is not None:
        bindings["TAVILY_API_KEY"] = f"{tavily_secret_name}:latest"
    return bindings


def _validate_final_settings(
    *,
    env_vars: dict[str, str],
    env_values: OrderedDict[str, str],
    secret_placeholders: dict[str, str],
) -> None:
    combined_env = dict(env_vars)
    for key in SENSITIVE_ENV_KEYS:
        local_value = (env_values.get(key, "") or "").strip()
        if local_value:
            combined_env[key] = local_value
    combined_env.update(secret_placeholders)
    with _temporary_environ(combined_env):
        settings = Settings.from_env()
        settings.validate_production_posture()
        settings.validate_storage_contract()


def _deploy_cloud_run_service(
    *,
    adapters: GCPAdapters,
    config: ResolvedDeployConfig,
    image_uri: str,
    service_account_email: str,
    env_vars: dict[str, str],
    secret_bindings: dict[str, str],
    sql_instance_ref: Any,
):
    result = adapters.cloud_run.deploy_service(
        project_id=config.project_id,
        region=config.region,
        service_name=config.service_name,
        image_uri=image_uri,
        service_account_email=service_account_email,
        env_vars=env_vars,
        secrets=secret_bindings,
        cloudsql_connection_name=sql_instance_ref.connection_name,
        timeout=DEFAULT_TIMEOUT,
        cpu=config.cpu,
        memory=config.memory,
        min_instances=config.min_instances,
        max_instances=config.max_instances,
        concurrency=config.concurrency,
        allow_unauthenticated=True,
        ingress=INGRESS_SETTING,
    )
    if not result.ok:
        raise DeployStageError(
            stage="cloud_run_deploy",
            message=_gcp_error_message(result.error, "Cloud Run deploy failed."),
            action=_gcp_error_action(result.error, "Inspect the Cloud Run error output and rerun deploy."),
        )
    assert result.value is not None
    return result.value


def _probe_liveness(service_url: str) -> bool:
    try:
        response = httpx.get(f"{service_url.rstrip('/')}/livez", timeout=10.0)
    except Exception:
        return False
    return response.status_code == 200


def _build_feature_summary(env_values: OrderedDict[str, str]) -> dict[str, object]:
    tooling_enabled = _parse_bool_string(env_values.get("REALTIME_TOOLING_ENABLED", "false"))
    return {
        "vision_memory": _parse_bool_string(env_values.get("VISION_MEMORY_ENABLED", "false")),
        "realtime_tooling": tooling_enabled,
        "web_search_provider": (
            env_values.get("REALTIME_WEB_SEARCH_PROVIDER")
            if tooling_enabled
            else None
        ),
    }


def _build_next_steps(
    *,
    service_url: str | None,
    project_id: str,
    region: str,
    bearer_secret_name: str,
) -> list[str]:
    if service_url is None:
        return [
            f"Run `portworld doctor --target gcp-cloud-run --project {project_id} --region {region}`",
        ]
    base_url = service_url.rstrip("/")
    return [
        f"curl {base_url}/livez",
        (
            "curl -H \"Authorization: Bearer $(gcloud secrets versions access latest "
            f"--secret={bearer_secret_name} --project={project_id})\" {base_url}/readyz"
        ),
        f"Run `portworld doctor --target gcp-cloud-run --project {project_id} --region {region}`",
    ]


def _build_success_message(
    *,
    config: ResolvedDeployConfig,
    service_url: str | None,
    image_uri: str,
    service_account_email: str,
    bucket_name: str,
    features: dict[str, object],
    next_steps: list[str],
) -> str:
    lines = [
        format_key_value_lines(
            ("project_id", config.project_id),
            ("region", config.region),
            ("service_name", config.service_name),
            ("service_url", service_url),
            ("image", image_uri),
            ("artifact_repository", config.artifact_repository),
            ("cloud_sql_instance", config.sql_instance_name),
            ("database_name", config.database_name),
            ("bucket_name", bucket_name),
            ("service_account", service_account_email),
            ("vision_memory", features.get("vision_memory")),
            ("realtime_tooling", features.get("realtime_tooling")),
            ("web_search_provider", features.get("web_search_provider")),
        )
    ]
    lines.append("next_steps:")
    for step in next_steps:
        lines.append(f"- {step}")
    return "\n".join(line for line in lines if line)


def _failure_result(
    *,
    stage: str,
    exc: Exception,
    stage_records: list[dict[str, object]],
    resources: dict[str, object],
    action: str | None,
    error_type: str,
    exit_code: int = 1,
) -> CommandResult:
    message = str(exc)
    checks = ()
    if action:
        checks = (
            DiagnosticCheck(
                id="next-step",
                status="warn",
                message=message,
                action=action,
            ),
        )
    return CommandResult(
        ok=False,
        command=COMMAND_NAME,
        message=f"stage: {stage}\nerror: {message}",
        data={
            "stage": stage,
            "error_type": error_type,
            "stages": stage_records,
            "resources": resources,
        },
        checks=checks,
        exit_code=exit_code,
    )


def _record_stage(
    stage_records: list[dict[str, object]],
    *,
    stage: str,
    message: str,
    details: dict[str, object] | None = None,
) -> None:
    payload: dict[str, object] = {"stage": stage, "status": "ok", "message": message}
    if details:
        payload["details"] = details
    stage_records.append(payload)


def _ensure_secret_version(
    *,
    adapters: GCPAdapters,
    project_id: str,
    secret_name: str,
    secret_value: str,
    stage: str,
) -> str:
    _ensure_secret_exists(
        adapters=adapters,
        project_id=project_id,
        secret_name=secret_name,
        stage=stage,
    )
    _add_secret_version(
        adapters=adapters,
        project_id=project_id,
        secret_name=secret_name,
        secret_value=secret_value,
        stage=stage,
    )
    return secret_name


def _ensure_secret_exists(
    *,
    adapters: GCPAdapters,
    project_id: str,
    secret_name: str,
    stage: str,
) -> None:
    result = adapters.secret_manager.create_secret(
        project_id=project_id,
        secret_name=secret_name,
    )
    if not result.ok:
        raise DeployStageError(
            stage=stage,
            message=_gcp_error_message(result.error, f"Failed creating secret {secret_name!r}."),
            action=_gcp_error_action(result.error, "Verify Secret Manager permissions and rerun deploy."),
        )


def _add_secret_version(
    *,
    adapters: GCPAdapters,
    project_id: str,
    secret_name: str,
    secret_value: str,
    stage: str,
) -> None:
    result = adapters.secret_manager.add_secret_version(
        project_id=project_id,
        secret_name=secret_name,
        secret_value=secret_value,
    )
    if not result.ok:
        raise DeployStageError(
            stage=stage,
            message=_gcp_error_message(result.error, f"Failed adding secret version for {secret_name!r}."),
            action=_gcp_error_action(result.error, "Verify Secret Manager permissions and rerun deploy."),
        )


def _required_env_value(env_values: OrderedDict[str, str], key: str) -> str:
    value = (env_values.get(key, "") or "").strip()
    if value:
        return value
    raise DeployUsageError(f"{key} is required for Cloud Run deploy but is missing from backend/.env.")


def _runtime_service_account_id(service_name: str) -> str:
    normalized = re.sub(r"[^a-z0-9-]+", "-", service_name.strip().lower()).strip("-")
    account_id = f"{normalized or 'portworld'}-runtime"
    account_id = re.sub(r"-{2,}", "-", account_id).strip("-")
    if len(account_id) > 30:
        account_id = account_id[:30].rstrip("-")
    if len(account_id) < 6:
        account_id = (account_id + "-runtime")[:6]
    return account_id


def _service_secret_name(service_name: str, suffix: str) -> str:
    normalized_service = re.sub(r"[^a-z0-9-]+", "-", service_name.strip().lower()).strip("-")
    return f"{normalized_service}-{suffix}"


def _default_bucket_name(project_id: str) -> str:
    candidate = f"{project_id.lower()}-{DEFAULT_BUCKET_SUFFIX}"
    candidate = re.sub(r"[^a-z0-9._-]+", "-", candidate).strip("-.")
    if len(candidate) <= 63:
        return candidate
    return candidate[:63].rstrip("-.")


def _default_image_tag(*, project_root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
            cwd=str(project_root),
        )
    except Exception:
        completed = None
    if completed is not None and completed.returncode == 0:
        sha = completed.stdout.strip()
        if sha:
            return sha
    return str(_now_ms())


def _generate_secure_token(*, length: int = 32) -> str:
    return secrets.token_urlsafe(length)


def _resolve_text_value(
    *,
    explicit: str | None,
    remembered: str | None,
    default: str,
) -> str:
    return _first_non_empty(explicit, remembered, default) or default


def _prompt_or_require_text(
    cli_context: CLIContext,
    *,
    prompt: str,
    value: str | None,
    error_message: str,
    default: str | None = None,
) -> str:
    if value is not None and value.strip():
        return value.strip()
    if cli_context.non_interactive:
        raise DeployUsageError(error_message)
    response = click.prompt(prompt, default=default, show_default=default is not None, type=str)
    normalized = response.strip()
    if not normalized:
        raise DeployUsageError(error_message)
    return normalized


def _first_non_empty(*values: str | None) -> str | None:
    for value in values:
        if value is not None and value.strip():
            return value.strip()
    return None


def _explicit_csv_or_none(raw_value: str | None) -> str | None:
    normalized = _first_non_empty(raw_value)
    if normalized is None or normalized == "*":
        return None
    return normalized


def _csv_or_none(values: tuple[str, ...]) -> str | None:
    normalized = _first_non_empty(*values)
    if normalized is None:
        return None
    return ",".join(value.strip() for value in values if value.strip())


def _parse_bool_string(raw_value: str) -> bool:
    return raw_value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _gcp_error_message(error: object | None, fallback: str) -> str:
    message = getattr(error, "message", None)
    if isinstance(message, str) and message.strip():
        return message
    return fallback


def _gcp_error_action(error: object | None, fallback: str) -> str:
    action = getattr(error, "action", None)
    if isinstance(action, str) and action.strip():
        return action
    return fallback


@contextmanager
def _temporary_environ(overrides: dict[str, str]) -> Iterator[None]:
    original = os.environ.copy()
    try:
        os.environ.clear()
        os.environ.update(original)
        os.environ.update(overrides)
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)


def _now_ms() -> int:
    return time_ns() // 1_000_000
