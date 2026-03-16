from __future__ import annotations

import click

from portworld_cli.context import CLIContext
from portworld_cli.init_runtime import InitOptions, run_init
from portworld_cli.output import exit_with_result


@click.command("init")
@click.option("--force", is_flag=True, default=False, help="Rewrite backend/.env without overwrite confirmation.")
@click.option("--with-vision", is_flag=True, default=False, help="Enable visual memory.")
@click.option("--without-vision", is_flag=True, default=False, help="Disable visual memory.")
@click.option("--with-tooling", is_flag=True, default=False, help="Enable realtime tooling.")
@click.option("--without-tooling", is_flag=True, default=False, help="Disable realtime tooling.")
@click.option("--openai-api-key", default=None, help="OpenAI API key for realtime sessions.")
@click.option("--vision-provider-api-key", default=None, help="Vision provider API key.")
@click.option("--tavily-api-key", default=None, help="Tavily API key for web search.")
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
@click.option("--stack-name", default=None, help="Published workspace stack name under ~/.portworld/stacks.")
@click.option(
    "--release-tag",
    default=None,
    help="Published backend release tag to pin (vX.Y.Z or latest).",
)
@click.option("--host-port", type=int, default=None, help="Host port for the published workspace backend.")
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
    backend_profile: str | None,
    cors_origins: str | None,
    allowed_hosts: str | None,
    bearer_token: str | None,
    generate_bearer_token: bool,
    clear_bearer_token: bool,
    project_mode: str | None,
    runtime_source: str | None,
    stack_name: str | None,
    release_tag: str | None,
    host_port: int | None,
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
                backend_profile=backend_profile,
                cors_origins=cors_origins,
                allowed_hosts=allowed_hosts,
                bearer_token=bearer_token,
                generate_bearer_token=generate_bearer_token,
                clear_bearer_token=clear_bearer_token,
                project_mode=project_mode,
                runtime_source=runtime_source,
                stack_name=stack_name,
                release_tag=release_tag,
                host_port=host_port,
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
