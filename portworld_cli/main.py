from __future__ import annotations

from pathlib import Path

import click

from portworld_cli.context import CLIContext
from portworld_cli.lazy_group import LazyGroup
from portworld_cli.version import __version__


COMMAND_SPECS: dict[str, tuple[str, str, str]] = {
    "init": ("portworld_cli.commands.init", "init_command", "Guide onboarding, configure PortWorld, and run the selected setup path."),
    "doctor": ("portworld_cli.commands.doctor", "doctor_command", "Validate local or managed deployment readiness."),
    "deploy": ("portworld_cli.commands.deploy", "deploy_group", "Deploy PortWorld to a managed target."),
    "status": ("portworld_cli.commands.status", "status_command", "Inspect workspace and deployment state."),
    "logs": ("portworld_cli.commands.logs", "logs_group", "Read managed deployment logs."),
    "config": ("portworld_cli.commands.config", "config_group", "Inspect or edit project configuration."),
    "providers": ("portworld_cli.commands.providers", "providers_group", "Inspect the currently supported provider surface."),
    "update": ("portworld_cli.commands.update", "update_group", "Update the CLI installation or the active managed deployment."),
    "ops": ("portworld_cli.commands.ops", "ops_group", "Run backend operator tasks."),
    "extensions": ("portworld_cli.commands.extensions", "extensions_group", "Manage official or local PortWorld extensions."),
}


@click.group(
    cls=LazyGroup,
    lazy_commands=COMMAND_SPECS,
    context_settings={"help_option_names": ["-h", "--help"]},
    no_args_is_help=True,
)
@click.option(
    "--project-root",
    "project_root",
    type=click.Path(path_type=Path, file_okay=False, dir_okay=True, resolve_path=True),
    default=None,
    help="Path to the PortWorld project or workspace root.",
)
@click.option("--verbose", is_flag=True, default=False, help="Enable verbose CLI output.")
@click.option("--json", "json_output", is_flag=True, default=False, help="Emit JSON output.")
@click.option(
    "--non-interactive",
    "non_interactive",
    is_flag=True,
    default=False,
    help="Fail instead of prompting for missing input.",
)
@click.option("--yes", "yes", is_flag=True, default=False, help="Accept confirmation prompts.")
@click.version_option(version=__version__, prog_name="portworld")
@click.pass_context
def cli(
    ctx: click.Context,
    project_root: Path | None,
    verbose: bool,
    json_output: bool,
    non_interactive: bool,
    yes: bool,
) -> None:
    """PortWorld backend deploy and operator CLI."""
    ctx.obj = CLIContext(
        project_root_override=project_root,
        verbose=verbose,
        json_output=json_output,
        non_interactive=non_interactive,
        yes=yes,
    )


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
