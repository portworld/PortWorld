from __future__ import annotations

import click

from portworld_cli.commands.compat import reject_legacy_secret_flag
from portworld_cli.providers.types import ProviderEditOptions
from portworld_cli.context import CLIContext
from portworld_cli.output import exit_with_result
from portworld_cli.providers.catalog import (
    PROVIDER_KIND_REALTIME,
    PROVIDER_KIND_SEARCH,
    PROVIDER_KIND_VISION,
    supported_runtime_provider_ids,
)
from portworld_cli.services.config.edit_service import (
    run_edit_cloud,
    run_edit_providers,
    run_edit_security,
)
from portworld_cli.services.config.show_service import run_config_show
from portworld_cli.services.config.types import CloudEditOptions, SecurityEditOptions


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
@click.option(
    "--realtime-provider",
    type=click.Choice(supported_runtime_provider_ids(PROVIDER_KIND_REALTIME)),
    default=None,
    help="Select the realtime provider id.",
)
@click.option("--with-vision", is_flag=True, default=False, help="Enable visual memory.")
@click.option("--without-vision", is_flag=True, default=False, help="Disable visual memory.")
@click.option(
    "--vision-provider",
    type=click.Choice(supported_runtime_provider_ids(PROVIDER_KIND_VISION)),
    default=None,
    help="Select the vision provider id when visual memory is enabled.",
)
@click.option("--with-tooling", is_flag=True, default=False, help="Enable realtime tooling.")
@click.option("--without-tooling", is_flag=True, default=False, help="Disable realtime tooling.")
@click.option(
    "--search-provider",
    type=click.Choice(supported_runtime_provider_ids(PROVIDER_KIND_SEARCH)),
    default=None,
    help="Select the web-search provider id when tooling is enabled.",
)
@click.option("--realtime-api-key", default=None, help="Realtime provider API key for the selected realtime provider.")
@click.option("--vision-api-key", default=None, help="Vision provider API key for the selected vision provider.")
@click.option("--search-api-key", default=None, help="Search provider API key for the selected search provider.")
@click.option(
    "--openai-api-key",
    default=None,
    hidden=True,
    expose_value=False,
    callback=reject_legacy_secret_flag,
)
@click.option(
    "--vision-provider-api-key",
    default=None,
    hidden=True,
    expose_value=False,
    callback=reject_legacy_secret_flag,
)
@click.option(
    "--tavily-api-key",
    default=None,
    hidden=True,
    expose_value=False,
    callback=reject_legacy_secret_flag,
)
@click.pass_obj
def config_edit_providers_command(
    cli_context: CLIContext,
    realtime_provider: str | None,
    with_vision: bool,
    without_vision: bool,
    vision_provider: str | None,
    with_tooling: bool,
    without_tooling: bool,
    search_provider: str | None,
    realtime_api_key: str | None,
    vision_api_key: str | None,
    search_api_key: str | None,
) -> None:
    """Edit provider choices, feature toggles, and related credentials."""
    exit_with_result(
        cli_context,
        run_edit_providers(
            cli_context,
            ProviderEditOptions(
                realtime_provider=realtime_provider,
                with_vision=with_vision,
                without_vision=without_vision,
                vision_provider=vision_provider,
                with_tooling=with_tooling,
                without_tooling=without_tooling,
                search_provider=search_provider,
                realtime_api_key=realtime_api_key,
                vision_api_key=vision_api_key,
                search_api_key=search_api_key,
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
@click.option("--bearer-token", default=None, help="Explicit bearer token to store in backend/.env.")
@click.option("--generate-bearer-token", is_flag=True, default=False, help="Generate a new bearer token.")
@click.option("--clear-bearer-token", is_flag=True, default=False, help="Clear the bearer token.")
@click.pass_obj
def config_edit_security_command(
    cli_context: CLIContext,
    backend_profile: str | None,
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
@click.option(
    "--cloud-provider",
    type=click.Choice(["gcp", "aws", "azure"]),
    default=None,
    help="Managed cloud provider to configure.",
)
@click.option(
    "--target",
    type=click.Choice(["gcp-cloud-run", "aws-ecs-fargate", "azure-container-apps"]),
    default=None,
    help="Managed target to configure.",
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
@click.option("--aws-region", default=None, help="Default AWS region.")
@click.option("--aws-service", default=None, help="Default AWS ECS service name.")
@click.option("--aws-vpc-id", default=None, help="Default VPC id.", hidden=True)
@click.option("--aws-subnet-ids", default=None, help="Default subnet ids (comma-separated).", hidden=True)
@click.option("--azure-subscription", default=None, help="Default Azure subscription id.")
@click.option("--azure-resource-group", default=None, help="Default Azure resource group.")
@click.option("--azure-region", default=None, help="Default Azure region.")
@click.option("--azure-environment", default=None, help="Default Container Apps environment name.")
@click.option("--azure-app", default=None, help="Default Container App name.")
@click.pass_obj
def config_edit_cloud_command(
    cli_context: CLIContext,
    project_mode: str | None,
    runtime_source: str | None,
    cloud_provider: str | None,
    target: str | None,
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
    aws_region: str | None,
    aws_service: str | None,
    aws_vpc_id: str | None,
    aws_subnet_ids: str | None,
    azure_subscription: str | None,
    azure_resource_group: str | None,
    azure_region: str | None,
    azure_environment: str | None,
    azure_app: str | None,
) -> None:
    """Edit project mode and managed cloud defaults."""
    exit_with_result(
        cli_context,
        run_edit_cloud(
            cli_context,
            CloudEditOptions(
                project_mode=project_mode,
                runtime_source=runtime_source,
                cloud_provider=cloud_provider,
                target=target,
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
                aws_region=aws_region,
                aws_service=aws_service,
                aws_vpc_id=aws_vpc_id,
                aws_subnet_ids=aws_subnet_ids,
                azure_subscription=azure_subscription,
                azure_resource_group=azure_resource_group,
                azure_region=azure_region,
                azure_environment=azure_environment,
                azure_app=azure_app,
            ),
        ),
    )
