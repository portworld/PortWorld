from __future__ import annotations

import click

from portworld_cli.context import CLIContext
from portworld_cli.output import exit_with_result
from portworld_cli.providers.service import run_providers_list, run_providers_show


@click.group("providers")
def providers_group() -> None:
    """Inspect the currently supported provider surface."""


@providers_group.command("list")
@click.pass_obj
def providers_list_command(cli_context: CLIContext) -> None:
    """List currently supported official providers."""
    exit_with_result(cli_context, run_providers_list(cli_context))


@providers_group.command("show")
@click.argument("provider_id")
@click.pass_obj
def providers_show_command(cli_context: CLIContext, provider_id: str) -> None:
    """Show setup and capability details for one provider."""
    exit_with_result(cli_context, run_providers_show(cli_context, provider_id))
