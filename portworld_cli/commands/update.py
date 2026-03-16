from __future__ import annotations

import click

from portworld_cli.context import CLIContext
from portworld_cli.deploy_runtime import DeployGCPCloudRunOptions
from portworld_cli.output import exit_with_result
from portworld_cli.update_runtime import run_update_cli, run_update_deploy


@click.group("update")
def update_group() -> None:
    """Update the CLI installation or the active managed deployment."""


@update_group.command("cli")
@click.option("--json", "json_output", is_flag=True, default=False, help="Emit JSON output.")
@click.pass_obj
def update_cli_command(cli_context: CLIContext, json_output: bool) -> None:
    """Show the recommended CLI upgrade command for this install mode."""
    if json_output:
        cli_context.json_output = True
    exit_with_result(cli_context, run_update_cli(cli_context))


@update_group.command("deploy")
@click.option("--project", default=None, help="Target GCP project id.")
@click.option("--region", default=None, help="Target GCP region.")
@click.option("--service", default=None, help="Cloud Run service name.")
@click.option("--artifact-repo", default=None, help="Artifact Registry repository name.")
@click.option("--sql-instance", default=None, help="Cloud SQL instance name.")
@click.option("--database", default=None, help="Cloud SQL database name.")
@click.option("--bucket", default=None, help="GCS bucket name for managed artifacts.")
@click.option("--cors-origins", default=None, help="Explicit production CORS origins (comma-separated).")
@click.option("--allowed-hosts", default=None, help="Explicit production allowed hosts (comma-separated).")
@click.option("--tag", default=None, help="Container image tag.")
@click.option("--min-instances", type=int, default=None, help="Minimum Cloud Run instances.")
@click.option("--max-instances", type=int, default=None, help="Maximum Cloud Run instances.")
@click.option("--concurrency", type=int, default=None, help="Cloud Run request concurrency.")
@click.option("--cpu", default=None, help="Cloud Run CPU setting, for example 1.")
@click.option("--memory", default=None, help="Cloud Run memory setting, for example 1Gi.")
@click.pass_obj
def update_deploy_command(
    cli_context: CLIContext,
    project: str | None,
    region: str | None,
    service: str | None,
    artifact_repo: str | None,
    sql_instance: str | None,
    database: str | None,
    bucket: str | None,
    cors_origins: str | None,
    allowed_hosts: str | None,
    tag: str | None,
    min_instances: int | None,
    max_instances: int | None,
    concurrency: int | None,
    cpu: str | None,
    memory: str | None,
) -> None:
    """Redeploy the active managed target using the current public deploy path."""
    exit_with_result(
        cli_context,
        run_update_deploy(
            cli_context,
            DeployGCPCloudRunOptions(
                project=project,
                region=region,
                service=service,
                artifact_repo=artifact_repo,
                sql_instance=sql_instance,
                database=database,
                bucket=bucket,
                cors_origins=cors_origins,
                allowed_hosts=allowed_hosts,
                tag=tag,
                min_instances=min_instances,
                max_instances=max_instances,
                concurrency=concurrency,
                cpu=cpu,
                memory=memory,
            ),
        ),
    )
