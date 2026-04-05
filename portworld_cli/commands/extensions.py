from __future__ import annotations

import click

from portworld_cli.context import CLIContext
from portworld_cli.output import exit_with_result
from portworld_cli.services.extensions import (
    run_extensions_add,
    run_extensions_disable,
    run_extensions_doctor,
    run_extensions_enable,
    run_extensions_list,
    run_extensions_remove,
    run_extensions_show,
)


@click.group("extensions")
def extensions_group() -> None:
    """Manage workspace extension manifests and extension install state."""


@extensions_group.command("list")
@click.pass_obj
def extensions_list_command(cli_context: CLIContext) -> None:
    """List official catalog entries and currently installed extensions."""
    exit_with_result(cli_context, run_extensions_list(cli_context))


@extensions_group.command("show")
@click.argument("extension_id")
@click.pass_obj
def extensions_show_command(cli_context: CLIContext, extension_id: str) -> None:
    """Show extension details for one extension id."""
    exit_with_result(cli_context, run_extensions_show(cli_context, extension_id))


@extensions_group.command("add")
@click.argument("extension_ref")
@click.pass_obj
def extensions_add_command(cli_context: CLIContext, extension_ref: str) -> None:
    """Add an extension from an official id or local definition JSON file path."""
    exit_with_result(cli_context, run_extensions_add(cli_context, extension_ref))


@extensions_group.command("remove")
@click.argument("extension_id")
@click.pass_obj
def extensions_remove_command(cli_context: CLIContext, extension_id: str) -> None:
    """Remove an installed extension."""
    exit_with_result(cli_context, run_extensions_remove(cli_context, extension_id))


@extensions_group.command("enable")
@click.argument("extension_id")
@click.pass_obj
def extensions_enable_command(cli_context: CLIContext, extension_id: str) -> None:
    """Enable an installed extension."""
    exit_with_result(
        cli_context,
        run_extensions_enable(cli_context, extension_id, enabled=True),
    )


@extensions_group.command("disable")
@click.argument("extension_id")
@click.pass_obj
def extensions_disable_command(cli_context: CLIContext, extension_id: str) -> None:
    """Disable an installed extension."""
    exit_with_result(cli_context, run_extensions_disable(cli_context, extension_id))


@extensions_group.command("doctor")
@click.argument("extension_id", required=False)
@click.pass_obj
def extensions_doctor_command(cli_context: CLIContext, extension_id: str | None) -> None:
    """Validate extension state and show install.sh remediation for missing prerequisites."""
    exit_with_result(cli_context, run_extensions_doctor(cli_context, extension_id))
