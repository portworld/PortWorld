from __future__ import annotations

from collections import OrderedDict

import click

from portworld_cli.azure.client import AzureAdapters
from portworld_cli.azure.common import azure_cli_available
from portworld_cli.azure.stages import (
    DeployAzureContainerAppsOptions,
    build_runtime_env_vars,
    now_ms,
    probe_livez,
    probe_ws,
    resolve_azure_deploy_config,
    run_azure_deploy_mutations,
    sanitize_runtime_env_for_output,
    stage_ok,
)
from portworld_cli.azure.stages.config import ResolvedAzureDeployConfig
from portworld_cli.context import CLIContext
from portworld_cli.deploy.config import DeployStageError, DeployUsageError, load_deploy_session
from portworld_cli.deploy.reporting import humanize_stage_label
from portworld_cli.deploy_state import DeployState, write_deploy_state
from portworld_cli.output import CommandResult
from portworld_cli.targets import TARGET_AZURE_CONTAINER_APPS
from portworld_cli.ux.prompts import prompt_confirm
from portworld_cli.ux.progress import ProgressReporter

COMMAND_NAME = "portworld deploy azure-container-apps"


def run_deploy_azure_container_apps(
    cli_context: CLIContext,
    options: DeployAzureContainerAppsOptions,
) -> CommandResult:
    resources: dict[str, object] = {}
    stage_records: list[dict[str, object]] = []
    progress = ProgressReporter(cli_context)
    try:
        with progress.stage(humanize_stage_label("repo_config_discovery")):
            session = load_deploy_session(cli_context)
            stage_records.append(stage_ok("repo_config_discovery", "Resolved workspace and loaded CLI config inputs."))

        with progress.stage(humanize_stage_label("prerequisite_validation")):
            if not azure_cli_available():
                raise DeployStageError(
                    stage="prerequisite_validation",
                    message="Azure CLI is not installed or not on PATH.",
                    action="Install Azure CLI and retry deploy.",
                )
            stage_records.append(stage_ok("prerequisite_validation", "Validated Azure CLI availability."))

        adapters = AzureAdapters.create()
        env_values = OrderedDict(session.merged_env_values().items())
        with progress.stage(humanize_stage_label("parameter_resolution")):
            config = resolve_azure_deploy_config(
                cli_context,
                options=options,
                env_values=env_values,
                project_config=session.project_config,
                runtime_source=session.effective_runtime_source,
                project_root=(None if session.project_paths is None else session.project_paths.project_root),
                adapters=adapters,
            )
            stage_records.append(stage_ok("parameter_resolution", "Resolved deploy parameters."))

        _confirm_mutations(cli_context, config)
        stage_records.append(stage_ok("mutation_plan", "Confirmed deploy mutations."))

        resources.update(
            {
                "subscription_id": config.subscription_id,
                "resource_group": config.resource_group,
                "region": config.region,
                "environment_name": config.environment_name,
                "app_name": config.app_name,
                "acr_name": config.acr_name,
                "image_uri": config.image_uri,
                "storage_account": config.storage_account,
                "blob_container": config.blob_container,
            }
        )

        deploy_result = run_azure_deploy_mutations(
            config=config,
            env_values=env_values,
            stage_records=stage_records,
            adapters=adapters,
            progress=progress,
        )
        fqdn = deploy_result.fqdn
        if fqdn is None:
            raise DeployStageError(
                stage="post_deploy_validation",
                message="Container Apps ingress FQDN was not found.",
                action="Ensure the app exists with external ingress enabled.",
            )
        service_url = f"https://{fqdn}"

        with progress.stage(humanize_stage_label("post_deploy_validation")):
            livez_ok = probe_livez(service_url)
            ws_ok = probe_ws(service_url, env_values.get("BACKEND_BEARER_TOKEN", ""))
            if not livez_ok:
                raise DeployStageError(
                    stage="post_deploy_validation",
                    message="Container Apps endpoint did not return 200 from /livez.",
                    action="Verify app revision health and ingress configuration.",
                )
            if not ws_ok:
                raise DeployStageError(
                    stage="post_deploy_validation",
                    message="Container Apps endpoint did not complete /ws/session websocket handshake.",
                    action="Verify ingress websocket behavior and Authorization header handling.",
                )
            stage_records.append(stage_ok("post_deploy_validation", "Validated /livez and /ws/session endpoint reachability."))

        with progress.stage(humanize_stage_label("state_write")):
            write_deploy_state(
                session.workspace_paths.state_file_for_target(TARGET_AZURE_CONTAINER_APPS),
                DeployState(
                    project_id=config.subscription_id,
                    region=config.region,
                    service_name=config.app_name,
                    runtime_source=config.runtime_source,
                    image_source_mode=config.image_source_mode,
                    artifact_repository=config.acr_repo,
                    artifact_repository_base=config.acr_repo,
                    cloud_sql_instance=None,
                    database_name=None,
                    bucket_name=config.blob_container,
                    image=deploy_result.image_uri,
                    published_release_tag=config.published_release_tag,
                    published_image_ref=config.published_image_ref,
                    service_url=service_url,
                    service_account_email=None,
                    last_deployed_at_ms=now_ms(),
                ),
            )
            stage_records.append(stage_ok("state_write", "Wrote Azure deploy state."))

        resources.update(
            {
                "subscription_id": config.subscription_id,
                "resource_group": config.resource_group,
                "region": config.region,
                "environment_name": config.environment_name,
                "app_name": config.app_name,
                "service_url": service_url,
                "image_uri": deploy_result.image_uri,
                "blob_container": config.blob_container,
            }
        )

        return CommandResult(
            ok=True,
            command=COMMAND_NAME,
            message="\n".join(
                [
                    f"target: {TARGET_AZURE_CONTAINER_APPS}",
                    f"subscription_id: {config.subscription_id}",
                    f"resource_group: {config.resource_group}",
                    f"region: {config.region}",
                    f"environment_name: {config.environment_name}",
                    f"app_name: {config.app_name}",
                    f"service_url: {service_url}",
                    f"image_source_mode: {config.image_source_mode}",
                    f"image_uri: {deploy_result.image_uri}",
                    "next_steps:",
                    f"- curl {service_url.rstrip('/')}/livez",
                    f"- portworld doctor --target azure-container-apps --azure-subscription {config.subscription_id}",
                ]
            ),
            data={
                "target": TARGET_AZURE_CONTAINER_APPS,
                "service_url": service_url,
                "runtime_source": config.runtime_source,
                "image_source_mode": config.image_source_mode,
                "published_release_tag": config.published_release_tag,
                "published_image_ref": config.published_image_ref,
                "resources": resources,
                "stages": stage_records,
                "runtime_env": sanitize_runtime_env_for_output(
                    build_runtime_env_vars(
                        env_values,
                        config,
                    )
                ),
            },
            exit_code=0,
        )
    except DeployUsageError as exc:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message=_problem_next_message(
                problem=str(exc),
                next_step=f"Run `{COMMAND_NAME} --help` and provide the required target inputs.",
                stage="parameter_resolution",
            ),
            data={
                "stage": "parameter_resolution",
                "error_type": type(exc).__name__,
                "resources": resources,
                "stages": stage_records,
            },
            exit_code=2,
        )
    except DeployStageError as exc:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message=_problem_next_message(
                problem=str(exc),
                next_step=exc.action or "Inspect the reported stage and rerun deploy.",
                stage=exc.stage,
            ),
            data={
                "stage": exc.stage,
                "error_type": type(exc).__name__,
                "resources": resources,
                "stages": stage_records,
            },
            exit_code=1,
        )
    except click.Abort:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message=_problem_next_message(
                problem="Deploy canceled before completion.",
                next_step=f"Rerun `{COMMAND_NAME}` when you are ready.",
                stage="mutation_plan",
            ),
            data={
                "stage": "mutation_plan",
                "error_type": "Abort",
                "resources": resources,
                "stages": stage_records,
            },
            exit_code=1,
        )
    finally:
        progress.close()


def _confirm_mutations(cli_context: CLIContext, config: ResolvedAzureDeployConfig) -> None:
    if cli_context.non_interactive or cli_context.yes:
        return
    confirmed = prompt_confirm(
        cli_context,
        message="\n".join(
            [
                "Proceed with Azure Container Apps deploy recording and validation?",
                f"subscription_id: {config.subscription_id}",
                f"resource_group: {config.resource_group}",
                f"environment: {config.environment_name}",
                f"app: {config.app_name}",
                f"acr: {config.acr_name}",
                f"storage_account: {config.storage_account}",
                f"image_uri: {config.image_uri}",
            ]
        ),
        default=True,
    )
    if not confirmed:
        raise click.Abort()


def _problem_next_message(*, problem: str, next_step: str, stage: str | None = None) -> str:
    lines: list[str] = []
    if stage:
        lines.append(f"stage: {stage}")
    lines.append(f"problem: {problem}")
    lines.append(f"next: {next_step}")
    return "\n".join(lines)
