from __future__ import annotations

from pathlib import Path

import click

from backend import __version__
from backend.cli_app.commands.config import config_group
from backend.cli_app.commands.deploy import deploy_group
from backend.cli_app.commands.doctor import doctor_command
from backend.cli_app.commands.init import init_command
from backend.cli_app.commands.logs import logs_group
from backend.cli_app.commands.ops import ops_group
from backend.cli_app.commands.providers import providers_group
from backend.cli_app.commands.status import status_command
from backend.cli_app.commands.update import update_group
from backend.cli_app.context import CLIContext


@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
    no_args_is_help=True,
)
@click.option(
    "--project-root",
    "project_root",
    type=click.Path(path_type=Path, file_okay=False, dir_okay=True, resolve_path=True),
    default=None,
    help="Path to the PortWorld project root.",
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


cli.add_command(init_command)
cli.add_command(doctor_command)
cli.add_command(deploy_group)
cli.add_command(status_command)
cli.add_command(logs_group)
cli.add_command(config_group)
cli.add_command(providers_group)
cli.add_command(update_group)
cli.add_command(ops_group)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
