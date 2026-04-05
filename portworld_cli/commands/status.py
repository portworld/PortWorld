from __future__ import annotations

import click

from portworld_cli.context import CLIContext
from portworld_cli.output import exit_with_result
from portworld_cli.services.status import run_status


@click.command("status")
@click.pass_obj
def status_command(cli_context: CLIContext) -> None:
    """Show current project and deploy inspection status."""
    exit_with_result(cli_context, run_status(cli_context))
