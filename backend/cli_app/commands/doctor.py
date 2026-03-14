from __future__ import annotations

import click

from backend.cli_app.context import CLIContext
from backend.cli_app.doctor_runtime import DoctorOptions, run_doctor
from backend.cli_app.output import exit_with_result


@click.command("doctor")
@click.option(
    "--target",
    type=click.Choice(["local", "gcp-cloud-run"]),
    default="local",
    show_default=True,
    help="Readiness target to validate.",
)
@click.option(
    "--full",
    is_flag=True,
    default=False,
    help="Run the storage bootstrap probe in addition to standard local checks.",
)
@click.option("--project", default=None, help="Target GCP project id for future gcp-cloud-run checks.")
@click.option("--region", default=None, help="Target GCP region for future gcp-cloud-run checks.")
@click.pass_obj
def doctor_command(
    cli_context: CLIContext,
    target: str,
    full: bool,
    project: str | None,
    region: str | None,
) -> None:
    """Validate local or managed deployment readiness."""
    exit_with_result(
        cli_context,
        run_doctor(
            cli_context,
            DoctorOptions(
                target=target,
                full=full,
                project=project,
                region=region,
            ),
        ),
    )
