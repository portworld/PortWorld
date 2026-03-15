from __future__ import annotations

import click

from backend.cli_app.context import CLIContext
from backend.cli_app.logs_runtime import LogsGCPCloudRunOptions, run_logs_gcp_cloud_run
from backend.cli_app.output import exit_with_result


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
