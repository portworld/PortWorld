from __future__ import annotations

import click

from backend.cli_app.context import CLIContext
from backend.cli_app.init_runtime import InitOptions, run_init
from backend.cli_app.output import exit_with_result


@click.command("init")
@click.option("--force", is_flag=True, default=False, help="Rewrite backend/.env without overwrite confirmation.")
@click.option("--with-vision", is_flag=True, default=False, help="Enable visual memory.")
@click.option("--without-vision", is_flag=True, default=False, help="Disable visual memory.")
@click.option("--with-tooling", is_flag=True, default=False, help="Enable realtime tooling.")
@click.option("--without-tooling", is_flag=True, default=False, help="Disable realtime tooling.")
@click.option("--openai-api-key", default=None, help="OpenAI API key for realtime sessions.")
@click.option("--vision-provider-api-key", default=None, help="Vision provider API key.")
@click.option("--tavily-api-key", default=None, help="Tavily API key for web search.")
@click.pass_obj
def init_command(
    cli_context: CLIContext,
    force: bool,
    with_vision: bool,
    without_vision: bool,
    with_tooling: bool,
    without_tooling: bool,
    openai_api_key: str | None,
    vision_provider_api_key: str | None,
    tavily_api_key: str | None,
) -> None:
    """Initialize local PortWorld backend configuration."""
    exit_with_result(
        cli_context,
        run_init(
            cli_context,
            InitOptions(
                force=force,
                with_vision=with_vision,
                without_vision=without_vision,
                with_tooling=with_tooling,
                without_tooling=without_tooling,
                openai_api_key=openai_api_key,
                vision_provider_api_key=vision_provider_api_key,
                tavily_api_key=tavily_api_key,
            ),
        ),
    )
