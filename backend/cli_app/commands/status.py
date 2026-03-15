from __future__ import annotations

import click

from backend.cli_app.context import CLIContext
from backend.cli_app.output import exit_with_result
from backend.cli_app.status_runtime import run_status


@click.command("status")
@click.pass_obj
def status_command(cli_context: CLIContext) -> None:
    """Show current project and deploy inspection status."""
    exit_with_result(cli_context, run_status(cli_context))
