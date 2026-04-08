from __future__ import annotations

import click

from portworld_cli.context import CLIContext
from portworld_cli.deploy.config import DeployStageError, ResolvedDeployConfig
from portworld_cli.deploy.gcp_errors import gcp_error_action, gcp_error_message
from portworld_cli.gcp import GCPAdapters, build_service_account_email
from portworld_cli.ux.prompts import prompt_text


def ensure_required_apis(*, adapters: GCPAdapters, config: ResolvedDeployConfig, required_services: tuple[str, ...]):
    statuses_result = adapters.service_usage.get_api_statuses(
        project_id=config.project_id,
        service_names=required_services,
    )
    if not statuses_result.ok:
        raise DeployStageError(
            stage="api_enablement",
            message=gcp_error_message(statuses_result.error, "Unable to inspect required GCP APIs."),
            action=gcp_error_action(statuses_result.error, "Verify project access and retry."),
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
                message=gcp_error_message(enable_result.error, "Failed enabling required GCP APIs."),
                action=gcp_error_action(enable_result.error, "Enable the listed APIs and rerun deploy."),
            )
        assert enable_result.value is not None
        return enable_result.value.resource
    return statuses


def ensure_runtime_service_account(*, adapters: GCPAdapters, config: ResolvedDeployConfig) -> str:
    account_id = _runtime_service_account_id(config.service_name)
    service_account_result = adapters.iam.create_service_account(
        project_id=config.project_id,
        account_id=account_id,
        display_name=f"{config.service_name} runtime",
    )
    if not service_account_result.ok:
        raise DeployStageError(
            stage="service_account_setup",
            message=gcp_error_message(service_account_result.error, "Failed creating runtime service account."),
            action=gcp_error_action(service_account_result.error, "Verify IAM permissions and rerun deploy."),
        )
    service_account_email = build_service_account_email(
        account_id=account_id,
        project_id=config.project_id,
    )
    for role in ("roles/secretmanager.secretAccessor",):
        bind_result = adapters.iam.bind_project_role(
            project_id=config.project_id,
            service_account_email=service_account_email,
            role=role,
        )
        if not bind_result.ok:
            raise DeployStageError(
                stage="service_account_setup",
                message=gcp_error_message(bind_result.error, f"Failed binding {role} to runtime service account."),
                action=gcp_error_action(bind_result.error, "Verify IAM permissions and rerun deploy."),
            )
    return service_account_email


def ensure_artifact_repository(
    *,
    adapters: GCPAdapters,
    config: ResolvedDeployConfig,
    image_source_mode_published_release: str,
    published_remote_repository_description: str,
    published_remote_repository_config_description: str,
    ghcr_remote_docker_repo: str,
):
    if config.image_source_mode == image_source_mode_published_release:
        result = adapters.artifact_registry.create_remote_repository(
            project_id=config.project_id,
            region=config.region,
            repository=config.artifact_repository,
            description=published_remote_repository_description,
            remote_description=published_remote_repository_config_description,
            remote_docker_repo=ghcr_remote_docker_repo,
        )
    else:
        result = adapters.artifact_registry.create_repository(
            project_id=config.project_id,
            region=config.region,
            repository=config.artifact_repository,
            description="PortWorld backend images",
        )
    if not result.ok:
        raise DeployStageError(
            stage="artifact_registry_setup",
            message=gcp_error_message(result.error, "Failed creating Artifact Registry repository."),
            action=gcp_error_action(result.error, "Verify Artifact Registry permissions and retry."),
        )
    assert result.value is not None
    return result.value.resource


def ensure_gcs_bucket(
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
            assert result.value is not None
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
                message=gcp_error_message(error, "Failed creating or reusing the artifact bucket."),
                action=gcp_error_action(
                    error,
                    "Provide --bucket with an alternative globally unique bucket name and retry.",
                ),
            )
        bucket_name = prompt_text(
            cli_context,
            message="Default bucket name is unavailable. Enter an alternative GCS bucket name",
            default="",
            show_default=False,
        ).strip()
        if not bucket_name:
            raise click.Abort()


def ensure_bucket_binding(
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
            message=gcp_error_message(result.error, "Failed binding bucket IAM role for the runtime service account."),
            action=gcp_error_action(result.error, "Verify bucket permissions and rerun deploy."),
        )


def _runtime_service_account_id(service_name: str) -> str:
    import re

    normalized = re.sub(r"[^a-z0-9-]+", "-", service_name.strip().lower()).strip("-")
    account_id = f"{normalized or 'portworld'}-runtime"
    account_id = re.sub(r"-{2,}", "-", account_id).strip("-")
    if len(account_id) > 30:
        account_id = account_id[:30].rstrip("-")
    if len(account_id) < 6:
        account_id = (account_id + "-runtime")[:6]
    return account_id
