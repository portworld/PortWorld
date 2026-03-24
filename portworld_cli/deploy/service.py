from __future__ import annotations

from collections import OrderedDict
from time import time_ns

import click
import httpx

from portworld_cli.context import CLIContext
from portworld_cli.deploy.config import (
    DeployGCPCloudRunOptions,
    DeployStageError,
    DeployUsageError,
    ResolvedDeployConfig,
    load_deploy_session,
    resolve_deploy_config,
)
from portworld_cli.deploy.reporting import (
    COMMAND_NAME,
    build_failure_result,
    build_feature_summary,
    build_next_steps,
    build_success_message,
    record_stage,
)
from portworld_cli.deploy.source import submit_source_build
from portworld_cli.deploy.stages.gcp_resources import (
    ensure_artifact_repository as stage_ensure_artifact_repository,
    ensure_bucket_binding as stage_ensure_bucket_binding,
    ensure_gcs_bucket as stage_ensure_gcs_bucket,
    ensure_required_apis as stage_ensure_required_apis,
    ensure_runtime_service_account as stage_ensure_runtime_service_account,
)
from portworld_cli.deploy.stages.runtime import (
    build_cloud_run_secret_bindings as stage_build_cloud_run_secret_bindings,
    build_runtime_env_vars as stage_build_runtime_env_vars,
    deploy_cloud_run_service as stage_deploy_cloud_run_service,
    ensure_cloud_sql as stage_ensure_cloud_sql,
    validate_final_settings as stage_validate_final_settings,
)
from portworld_cli.deploy.stages.secrets import ensure_core_secrets as stage_ensure_core_secrets
from portworld_cli.deploy_artifacts import (
    GHCR_REMOTE_DOCKER_REPO,
    IMAGE_SOURCE_MODE_PUBLISHED_RELEASE,
    IMAGE_SOURCE_MODE_SOURCE_BUILD,
)
from portworld_cli.deploy_state import DeployState, write_deploy_state
from portworld_cli.envfile import EnvFileParseError
from portworld_cli.gcp import GCPAdapters, REQUIRED_GCP_SERVICES
from portworld_cli.output import CommandResult, DiagnosticCheck
from portworld_cli.workspace.project_config import (
    GCP_CLOUD_RUN_TARGET,
    ProjectConfigError,
)
from portworld_cli.services.config.errors import ConfigRuntimeError
from portworld_cli.workspace.discovery.paths import ProjectRootResolutionError
from portworld_cli.workspace.state.state_store import CLIStateDecodeError, CLIStateTypeError

DEFAULT_TIMEOUT = "3600s"
DEFAULT_SQL_DATABASE_VERSION = "POSTGRES_16"
DEFAULT_SQL_CPU_COUNT = 1
DEFAULT_SQL_MEMORY = "3840MiB"
DEFAULT_SQL_USER_NAME = "portworld_app"
INGRESS_SETTING = "all"
PUBLISHED_REMOTE_REPOSITORY_DESCRIPTION = "PortWorld published backend image mirror"
PUBLISHED_REMOTE_REPOSITORY_CONFIG_DESCRIPTION = "Remote Docker repository proxying ghcr.io"

def run_deploy_gcp_cloud_run(
    cli_context: CLIContext,
    options: DeployGCPCloudRunOptions,
) -> CommandResult:
    stage_records: list[dict[str, object]] = []
    resources: dict[str, object] = {}
    checks: list[DiagnosticCheck] = []

    try:
        session = load_deploy_session(cli_context)
        env_values = OrderedDict(session.merged_env_values().items())
        project_config = session.project_config
        remembered_state = DeployState.from_payload(session.remembered_deploy_state)
        record_stage(
            stage_records,
            stage="repo_config_discovery",
            message="Resolved workspace and loaded CLI config inputs.",
            details={
                "workspace_root": str(session.workspace_root),
                "project_root": (
                    None if session.project_paths is None else str(session.project_paths.project_root)
                ),
                "workspace_resolution_source": session.workspace_resolution_source,
                "active_workspace_root": (
                    None if session.active_workspace_root is None else str(session.active_workspace_root)
                ),
                "env_file": None if session.env_path is None else str(session.env_path),
                "project_config_file": str(session.workspace_paths.project_config_file),
                "state_file": str(session.workspace_paths.state_file_for_target(GCP_CLOUD_RUN_TARGET)),
                "runtime_source": session.effective_runtime_source,
            },
        )

        adapters = GCPAdapters.create()
        active_account = _require_active_gcloud_account(adapters=adapters)
        record_stage(
            stage_records,
            stage="prerequisite_validation",
            message="Validated gcloud installation and authentication.",
            details={"account": active_account},
        )

        config = resolve_deploy_config(
            cli_context,
            adapters=adapters,
            env_values=env_values,
            project_config=project_config,
            remembered_state=remembered_state,
            options=options,
            runtime_source=session.effective_runtime_source,
            project_root=(None if session.project_paths is None else session.project_paths.project_root),
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
        record_stage(
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
                "runtime_source": config.runtime_source,
                "image_source_mode": config.image_source_mode,
                "image_tag": config.image_tag,
                "deploy_image_uri": config.deploy_image_uri,
                "published_release_tag": config.published_release_tag,
                "published_image_ref": config.published_image_ref,
            },
        )

        _confirm_mutations(cli_context, config=config)

        api_statuses = stage_ensure_required_apis(
            adapters=adapters,
            config=config,
            required_services=REQUIRED_GCP_SERVICES,
        )
        record_stage(
            stage_records,
            stage="api_enablement",
            message="Verified required GCP APIs are enabled.",
            details={"required_apis": [status.service_name for status in api_statuses]},
        )

        service_account_email = stage_ensure_runtime_service_account(
            adapters=adapters,
            config=config,
        )
        resources["service_account"] = service_account_email
        record_stage(
            stage_records,
            stage="service_account_setup",
            message="Ensured runtime service account and project IAM bindings.",
            details={"service_account_email": service_account_email},
        )

        repository_ref = stage_ensure_artifact_repository(
            adapters=adapters,
            config=config,
            image_source_mode_published_release=IMAGE_SOURCE_MODE_PUBLISHED_RELEASE,
            published_remote_repository_description=PUBLISHED_REMOTE_REPOSITORY_DESCRIPTION,
            published_remote_repository_config_description=PUBLISHED_REMOTE_REPOSITORY_CONFIG_DESCRIPTION,
            ghcr_remote_docker_repo=GHCR_REMOTE_DOCKER_REPO,
        )
        resources["artifact_registry_repository"] = repository_ref.repository
        record_stage(
            stage_records,
            stage="artifact_registry_setup",
            message="Ensured Artifact Registry repository exists.",
            details={
                "repository": repository_ref.repository,
                "mode": repository_ref.mode,
            },
        )

        image_uri = config.deploy_image_uri
        resources["image"] = image_uri
        if config.image_source_mode == IMAGE_SOURCE_MODE_SOURCE_BUILD:
            assert session.project_paths is not None
            build_result = submit_source_build(
                adapters=adapters,
                project_root=session.project_paths.project_root,
                dockerfile_path=session.project_paths.dockerfile,
                project_id=config.project_id,
                image_uri=image_uri,
            )
            if not build_result.ok:
                raise DeployStageError(
                    stage="cloud_build",
                    message=_gcp_error_message(build_result.error, "Cloud Build submission failed."),
                    action=_gcp_error_action(build_result.error, "Inspect the Cloud Build error output and rerun deploy."),
                )
            build_submission = build_result.value
            assert build_submission is not None
            record_stage(
                stage_records,
                stage="cloud_build",
                message="Built and published the backend image.",
                details={
                    "image": image_uri,
                    "build_id": build_submission.build_id,
                    "log_url": build_submission.log_url,
                },
            )
        else:
            record_stage(
                stage_records,
                stage="published_image_resolution",
                message="Resolved the pinned published backend image for managed deploy.",
                details={
                    "image": image_uri,
                    "published_release_tag": config.published_release_tag,
                    "published_image_ref": config.published_image_ref,
                },
            )

        (
            non_db_secret_names,
            provider_secret_names,
            provider_secret_values,
            bearer_secret_name,
            bearer_token_for_validation,
        ) = stage_ensure_core_secrets(
            adapters=adapters,
            config=config,
            env_values=env_values,
        )
        record_stage(
            stage_records,
            stage="secret_manager_setup",
            message="Ensured non-database runtime secrets exist.",
            details={"secrets": non_db_secret_names},
        )

        sql_instance_ref, database_url_secret_name, database_url_for_validation = stage_ensure_cloud_sql(
            adapters=adapters,
            config=config,
            default_sql_database_version=DEFAULT_SQL_DATABASE_VERSION,
            default_sql_cpu_count=DEFAULT_SQL_CPU_COUNT,
            default_sql_memory=DEFAULT_SQL_MEMORY,
            default_sql_user_name=DEFAULT_SQL_USER_NAME,
        )
        resources["cloud_sql_instance"] = sql_instance_ref.instance_name
        record_stage(
            stage_records,
            stage="cloud_sql_setup",
            message="Ensured Cloud SQL instance, database, user, and database URL secret for operational runtime metadata.",
            details={
                "instance_name": sql_instance_ref.instance_name,
                "connection_name": sql_instance_ref.connection_name,
                "database_url_secret_name": database_url_secret_name,
                "cloud_sql_role": "operational_metadata",
            },
        )

        bucket_name = stage_ensure_gcs_bucket(adapters=adapters, cli_context=cli_context, config=config)
        resources["bucket_name"] = bucket_name
        stage_ensure_bucket_binding(
            adapters=adapters,
            bucket_name=bucket_name,
            service_account_email=service_account_email,
        )
        record_stage(
            stage_records,
            stage="gcs_bucket_setup",
            message="Ensured managed object-store bucket and bucket IAM binding.",
            details={
                "bucket_name": bucket_name,
                "memory_source_of_truth": "object_store_files",
            },
        )

        env_vars = stage_build_runtime_env_vars(
            env_values=env_values,
            config=config,
            bucket_name=bucket_name,
        )
        secret_bindings = stage_build_cloud_run_secret_bindings(
            provider_secret_names=provider_secret_names,
            bearer_secret_name=bearer_secret_name,
            database_url_secret_name=database_url_secret_name,
        )
        stage_validate_final_settings(
            env_vars=env_vars,
            env_values=env_values,
            secret_placeholders={
                **provider_secret_values,
                "BACKEND_BEARER_TOKEN": bearer_token_for_validation,
                "BACKEND_DATABASE_URL": database_url_for_validation,
            },
        )
        record_stage(
            stage_records,
            stage="runtime_config_assembly",
            message="Assembled managed Cloud Run runtime configuration.",
            details={
                "env_var_count": len(env_vars),
                "secret_binding_count": len(secret_bindings),
                "storage_backend": "managed",
                "object_store_provider": "gcs",
            },
        )

        deploy_outcome = stage_deploy_cloud_run_service(
            adapters=adapters,
            config=config,
            image_uri=image_uri,
            service_account_email=service_account_email,
            env_vars=env_vars,
            secret_bindings=secret_bindings,
            sql_instance_ref=sql_instance_ref,
            default_timeout=DEFAULT_TIMEOUT,
            ingress_setting=INGRESS_SETTING,
        )
        service_ref = deploy_outcome.resource
        resources["service_name"] = service_ref.service_name
        resources["service_url"] = service_ref.url
        record_stage(
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
                        message="Cloud Run service deployed, but the final /livez probe did not succeed.",
                        action="Wait for the revision to finish starting, then re-run the health check command from the summary.",
                    )
                )
        record_stage(
            stage_records,
            stage="post_deploy_validation",
            message="Collected final deploy summary and follow-up commands.",
            details={
                "service_url": service_ref.url,
                "liveness_probe_ok": liveness_probe_ok,
            },
        )

        write_deploy_state(
            session.workspace_paths.state_file_for_target(GCP_CLOUD_RUN_TARGET),
            DeployState(
                project_id=config.project_id,
                region=config.region,
                service_name=config.service_name,
                runtime_source=config.runtime_source,
                image_source_mode=config.image_source_mode,
                artifact_repository=config.artifact_repository_base,
                artifact_repository_base=config.artifact_repository_base,
                cloud_sql_instance=config.sql_instance_name,
                database_name=config.database_name,
                bucket_name=bucket_name,
                image=image_uri,
                published_release_tag=config.published_release_tag,
                published_image_ref=config.published_image_ref,
                service_url=service_ref.url,
                service_account_email=service_account_email,
                last_deployed_at_ms=_now_ms(),
            ),
        )

        features = build_feature_summary(env_values)
        next_steps = build_next_steps(
            service_url=service_ref.url,
            project_id=config.project_id,
            region=config.region,
            bearer_secret_name=bearer_secret_name,
        )
        message = build_success_message(
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
                "workspace_root": str(session.workspace_root),
                "project_root": (
                    None if session.project_paths is None else str(session.project_paths.project_root)
                ),
                "workspace_resolution_source": session.workspace_resolution_source,
                "active_workspace_root": (
                    None if session.active_workspace_root is None else str(session.active_workspace_root)
                ),
                "project_id": config.project_id,
                "region": config.region,
                "service_name": config.service_name,
                "service_url": service_ref.url,
                "image": image_uri,
                "runtime_source": config.runtime_source,
                "image_source_mode": config.image_source_mode,
                "published_release_tag": config.published_release_tag,
                "published_image_ref": config.published_image_ref,
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
        return build_failure_result(
            stage="repo_config_discovery",
            exc=exc,
            stage_records=stage_records,
            resources=resources,
            action="Run from a PortWorld repo checkout, a published workspace, or pass --project-root.",
            error_type=type(exc).__name__,
        )
    except (
        EnvFileParseError,
        CLIStateDecodeError,
        CLIStateTypeError,
        DeployUsageError,
        ProjectConfigError,
        ConfigRuntimeError,
    ) as exc:
        return build_failure_result(
            stage="parameter_resolution",
            exc=exc,
            stage_records=stage_records,
            resources=resources,
            action=None,
            error_type=type(exc).__name__,
            exit_code=2,
        )
    except DeployStageError as exc:
        return build_failure_result(
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
        return build_failure_result(
            stage="deploy",
            exc=exc,
            stage_records=stage_records,
            resources=resources,
            action=None,
            error_type=type(exc).__name__,
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
            f"runtime_source: {config.runtime_source}",
            f"image_source_mode: {config.image_source_mode}",
            f"published_release_tag: {config.published_release_tag}" if config.published_release_tag else "",
            f"artifact_repo: {config.artifact_repository}",
            f"sql_instance: {config.sql_instance_name}",
            f"bucket: {config.bucket_name}",
        ]
    )
    confirmed = click.confirm(message, default=True, show_default=True)
    if not confirmed:
        raise click.Abort()


def _probe_liveness(service_url: str) -> bool:
    try:
        response = httpx.get(f"{service_url.rstrip('/')}/livez", timeout=10.0)
    except Exception:
        return False
    return response.status_code == 200


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


def _now_ms() -> int:
    return time_ns() // 1_000_000
