from __future__ import annotations

import click

from portworld_cli.commands.compat import reject_legacy_secret_flag
from portworld_cli.context import CLIContext
from portworld_cli.output import exit_with_result
from portworld_cli.providers.catalog import (
    PROVIDER_KIND_REALTIME,
    PROVIDER_KIND_SEARCH,
    PROVIDER_KIND_VISION,
    supported_runtime_provider_ids,
)
from portworld_cli.services.init import InitOptions, run_init


@click.command("init")
@click.option("--force", is_flag=True, default=False, help="Rewrite backend/.env without overwrite confirmation.")
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
@click.option(
    "--backend-profile",
    type=click.Choice(["development", "production"]),
    default=None,
    help="Default backend profile.",
)
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
    "--setup-mode",
    type=click.Choice(["quickstart", "manual"]),
    default=None,
    help="Interactive setup mode (quickstart or manual).",
)
@click.option(
    "--runtime-source",
    type=click.Choice(["source", "published"]),
    default=None,
    help="Advanced runtime source override for this workspace.",
)
@click.option(
    "--local-runtime",
    type=click.Choice(["source", "published"]),
    default=None,
    help="Advanced local runtime override. Interactive init defaults to published.",
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
def init_command(
    cli_context: CLIContext,
    force: bool,
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
    backend_profile: str | None,
    bearer_token: str | None,
    generate_bearer_token: bool,
    clear_bearer_token: bool,
    setup_mode: str | None,
    project_mode: str | None,
    runtime_source: str | None,
    local_runtime: str | None,
    cloud_provider: str | None,
    target: str | None,
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
    """Guide PortWorld onboarding, configure the workspace, and run the selected setup path."""
    exit_with_result(
        cli_context,
        run_init(
            cli_context,
            InitOptions(
                force=force,
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
                backend_profile=backend_profile,
                bearer_token=bearer_token,
                generate_bearer_token=generate_bearer_token,
                clear_bearer_token=clear_bearer_token,
                setup_mode=setup_mode,
                project_mode=project_mode,
                runtime_source=runtime_source,
                local_runtime=local_runtime,
                cloud_provider=cloud_provider,
                target=target,
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
