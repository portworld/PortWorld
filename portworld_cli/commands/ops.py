from __future__ import annotations

from pathlib import Path

import click

from portworld_cli.context import CLIContext
from portworld_cli.output import exit_with_result
from portworld_cli.services.ops import (
    run_bootstrap_storage,
    run_check_config,
    run_export_memory,
    run_migrate_storage_layout,
)
from portworld_cli.services.ops.service import run_memory_maintenance


@click.group("ops")
def ops_group() -> None:
    """Run backend operator tasks."""


@ops_group.command("check-config")
@click.option(
    "--full-readiness",
    is_flag=True,
    default=False,
    help="Run full readiness checks, including a storage bootstrap probe.",
)
@click.pass_obj
def check_config_command(cli_context: CLIContext, full_readiness: bool) -> None:
    """Validate backend configuration."""
    exit_with_result(
        cli_context,
        run_check_config(cli_context, full_readiness=full_readiness),
    )


@ops_group.command("bootstrap-storage")
@click.pass_obj
def bootstrap_storage_command(cli_context: CLIContext) -> None:
    """Create storage directories and schema."""
    exit_with_result(cli_context, run_bootstrap_storage(cli_context))


@ops_group.command("export-memory")
@click.option(
    "--output",
    type=click.Path(path_type=Path, dir_okay=False, resolve_path=False),
    default=None,
    help="Write the export ZIP to a specific path.",
)
@click.pass_obj
def export_memory_command(cli_context: CLIContext, output: Path | None) -> None:
    """Export backend memory artifacts."""
    exit_with_result(
        cli_context,
        run_export_memory(cli_context, output_path=output),
    )


@ops_group.command("migrate-storage-layout")
@click.pass_obj
def migrate_storage_layout_command(cli_context: CLIContext) -> None:
    """Migrate legacy storage layout artifacts."""
    exit_with_result(cli_context, run_migrate_storage_layout(cli_context))


@ops_group.group("memory-maintenance")
def memory_maintenance_group() -> None:
    """Run memory v2 maintenance operations."""


@memory_maintenance_group.command("run")
@click.option(
    "--scope",
    type=click.Choice(("global", "session"), case_sensitive=True),
    default="global",
    show_default=True,
    help="Run across all sessions or for a single session.",
)
@click.option(
    "--session-id",
    default=None,
    help="Required when --scope=session.",
)
@click.option(
    "--phase",
    type=click.Choice(("full", "candidates", "observations", "retrieval", "decay"), case_sensitive=True),
    default="full",
    show_default=True,
    help="Limit maintenance to a specific phase.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Compute maintenance decisions without persisting changes.",
)
@click.pass_obj
def memory_maintenance_run_command(
    cli_context: CLIContext,
    scope: str,
    session_id: str | None,
    phase: str,
    dry_run: bool,
) -> None:
    """Run memory v2 maintenance and promotion phases."""
    if scope == "session" and not session_id:
        raise click.UsageError("--session-id is required when --scope=session.")
    exit_with_result(
        cli_context,
        run_memory_maintenance(
            cli_context,
            scope=scope,
            session_id=session_id,
            phase=phase,
            dry_run=dry_run,
        ),
    )
