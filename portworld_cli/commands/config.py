from __future__ import annotations

import click

from portworld_cli.providers.types import ProviderEditOptions
from portworld_cli.services.config import (
    CloudEditOptions,
    SecurityEditOptions,
    run_config_show,
    run_edit_cloud,
    run_edit_providers,
    run_edit_security,
)
from portworld_cli.context import CLIContext
from portworld_cli.output import exit_with_result


@click.group("config")
def config_group() -> None:
    """Inspect or edit project configuration."""


@config_group.command("show")
@click.pass_obj
def config_show_command(cli_context: CLIContext) -> None:
    """Show the current project configuration."""
    exit_with_result(cli_context, run_config_show(cli_context))


@config_group.group("edit")
def config_edit_group() -> None:
    """Edit one configuration section."""


@config_edit_group.command("providers")
@click.option("--with-vision", is_flag=True, default=False, help="Enable visual memory.")
@click.option("--without-vision", is_flag=True, default=False, help="Disable visual memory.")
@click.option("--with-tooling", is_flag=True, default=False, help="Enable realtime tooling.")
@click.option("--without-tooling", is_flag=True, default=False, help="Disable realtime tooling.")
@click.option("--openai-api-key", default=None, help="OpenAI API key for realtime sessions.")
@click.option("--vision-provider-api-key", default=None, help="Vision provider API key.")
@click.option("--tavily-api-key", default=None, help="Tavily API key for web search.")
@click.pass_obj
def config_edit_providers_command(
    cli_context: CLIContext,
    with_vision: bool,
    without_vision: bool,
    with_tooling: bool,
    without_tooling: bool,
    openai_api_key: str | None,
    vision_provider_api_key: str | None,
    tavily_api_key: str | None,
) -> None:
    """Edit provider choices, feature toggles, and related credentials."""
    exit_with_result(
        cli_context,
        run_edit_providers(
            cli_context,
            ProviderEditOptions(
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


@config_edit_group.command("security")
@click.option(
    "--backend-profile",
    type=click.Choice(["development", "production"]),
    default=None,
    help="Default backend profile.",
)
@click.option("--cors-origins", default=None, help="Explicit CORS origins (comma-separated).")
@click.option("--allowed-hosts", default=None, help="Explicit allowed hosts (comma-separated).")
@click.option("--bearer-token", default=None, help="Explicit bearer token to store in backend/.env.")
@click.option("--generate-bearer-token", is_flag=True, default=False, help="Generate a new bearer token.")
@click.option("--clear-bearer-token", is_flag=True, default=False, help="Clear the bearer token.")
@click.pass_obj
def config_edit_security_command(
    cli_context: CLIContext,
    backend_profile: str | None,
    cors_origins: str | None,
    allowed_hosts: str | None,
    bearer_token: str | None,
    generate_bearer_token: bool,
    clear_bearer_token: bool,
) -> None:
    """Edit security defaults and local bearer token behavior."""
    exit_with_result(
        cli_context,
        run_edit_security(
            cli_context,
            SecurityEditOptions(
                backend_profile=backend_profile,
                cors_origins=cors_origins,
                allowed_hosts=allowed_hosts,
                bearer_token=bearer_token,
                generate_bearer_token=generate_bearer_token,
                clear_bearer_token=clear_bearer_token,
            ),
        ),
    )


@config_edit_group.command("cloud")
@click.option(
    "--project-mode",
    type=click.Choice(["local", "managed"]),
    default=None,
    help="Project mode for this repo.",
)
@click.option(
    "--runtime-source",
    type=click.Choice(["source", "published"]),
    default=None,
    help="Runtime source mode for this workspace.",
)
@click.option("--project", default=None, help="Default GCP project id.")
@click.option("--region", default=None, help="Default GCP region.")
@click.option("--service", default=None, help="Default Cloud Run service name.")
@click.option("--artifact-repo", default=None, help="Default Artifact Registry repository.")
@click.option("--sql-instance", default=None, help="Default Cloud SQL instance name.")
@click.option("--database", default=None, help="Default Cloud SQL database name.")
@click.option("--bucket", default=None, help="Default GCS bucket name.")
@click.option("--min-instances", type=int, default=None, help="Default Cloud Run minimum instances.")
@click.option("--max-instances", type=int, default=None, help="Default Cloud Run maximum instances.")
@click.option("--concurrency", type=int, default=None, help="Default Cloud Run concurrency.")
@click.option("--cpu", default=None, help="Default Cloud Run CPU.")
@click.option("--memory", default=None, help="Default Cloud Run memory.")
@click.pass_obj
def config_edit_cloud_command(
    cli_context: CLIContext,
    project_mode: str | None,
    runtime_source: str | None,
    project: str | None,
    region: str | None,
    service: str | None,
    artifact_repo: str | None,
    sql_instance: str | None,
    database: str | None,
    bucket: str | None,
    min_instances: int | None,
    max_instances: int | None,
    concurrency: int | None,
    cpu: str | None,
    memory: str | None,
) -> None:
    """Edit project mode and managed cloud defaults."""
    exit_with_result(
        cli_context,
        run_edit_cloud(
            cli_context,
            CloudEditOptions(
                project_mode=project_mode,
                runtime_source=runtime_source,
                project=project,
                region=region,
                service=service,
                artifact_repo=artifact_repo,
                sql_instance=sql_instance,
                database=database,
                bucket=bucket,
                min_instances=min_instances,
                max_instances=max_instances,
                concurrency=concurrency,
                cpu=cpu,
                memory=memory,
            ),
        ),
    )
