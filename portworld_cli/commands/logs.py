from __future__ import annotations

import click

from portworld_cli.context import CLIContext
from portworld_cli.output import exit_with_result
from portworld_cli.services.logs import (
    LogsAWSECSFargateOptions,
    LogsAzureContainerAppsOptions,
    LogsGCPCloudRunOptions,
    run_logs_aws_ecs_fargate,
    run_logs_azure_container_apps,
    run_logs_gcp_cloud_run,
)


SEVERITY_CHOICES = (
    "DEFAULT",
    "DEBUG",
    "INFO",
    "NOTICE",
    "WARNING",
    "ERROR",
    "CRITICAL",
    "ALERT",
    "EMERGENCY",
)


@click.group("logs")
def logs_group() -> None:
    """Read managed deployment logs."""


@logs_group.command("gcp-cloud-run")
@click.option("--project", default=None, help="Target GCP project id.")
@click.option("--region", default=None, help="Target GCP region.")
@click.option("--service", default=None, help="Cloud Run service name.")
@click.option("--since", default="24h", show_default=True, help="Freshness window for the log query.")
@click.option("--limit", type=int, default=50, show_default=True, help="Maximum number of log entries to return.")
@click.option(
    "--severity",
    type=click.Choice(SEVERITY_CHOICES, case_sensitive=False),
    default=None,
    help="Minimum severity to include.",
)
@click.pass_obj
def logs_gcp_cloud_run_command(
    cli_context: CLIContext,
    project: str | None,
    region: str | None,
    service: str | None,
    since: str,
    limit: int,
    severity: str | None,
) -> None:
    """Read Cloud Run logs for the current PortWorld deployment."""
    exit_with_result(
        cli_context,
        run_logs_gcp_cloud_run(
            cli_context,
            LogsGCPCloudRunOptions(
                project=project,
                region=region,
                service=service,
                since=since,
                limit=limit,
                severity=severity.upper() if severity is not None else None,
            ),
        ),
    )


@logs_group.command("aws-ecs-fargate")
@click.option("--region", default=None, help="Target AWS region.")
@click.option("--service", default=None, help="ECS service name.")
@click.option("--since", default="24h", show_default=True, help="Freshness window for the log query.")
@click.option("--limit", type=int, default=50, show_default=True, help="Maximum number of log entries to return.")
@click.pass_obj
def logs_aws_ecs_fargate_command(
    cli_context: CLIContext,
    region: str | None,
    service: str | None,
    since: str,
    limit: int,
) -> None:
    """Read ECS/Fargate CloudWatch logs for the current PortWorld deployment."""
    exit_with_result(
        cli_context,
        run_logs_aws_ecs_fargate(
            cli_context,
            LogsAWSECSFargateOptions(
                region=region,
                service=service,
                since=since,
                limit=limit,
            ),
        ),
    )


@logs_group.command("azure-container-apps")
@click.option("--subscription", default=None, help="Target Azure subscription id.")
@click.option("--resource-group", default=None, help="Target Azure resource group.")
@click.option("--app", default=None, help="Container App name.")
@click.option("--since", default="24h", show_default=True, help="Freshness window for the log query.")
@click.option("--limit", type=int, default=50, show_default=True, help="Maximum number of log entries to return.")
@click.pass_obj
def logs_azure_container_apps_command(
    cli_context: CLIContext,
    subscription: str | None,
    resource_group: str | None,
    app: str | None,
    since: str,
    limit: int,
) -> None:
    """Read Azure Container Apps logs for the current PortWorld deployment."""
    exit_with_result(
        cli_context,
        run_logs_azure_container_apps(
            cli_context,
            LogsAzureContainerAppsOptions(
                subscription=subscription,
                resource_group=resource_group,
                app=app,
                since=since,
                limit=limit,
            ),
        ),
    )
