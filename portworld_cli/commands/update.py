from __future__ import annotations

import click

from portworld_cli.context import CLIContext
from portworld_cli.output import exit_with_result
from portworld_cli.services.update import run_update_cli, run_update_deploy
from portworld_cli.services.update.service import UpdateDeployOptions


@click.group("update")
def update_group() -> None:
    """Update the CLI installation or the active managed deployment."""


@update_group.command("cli")
@click.option("--json", "json_output", is_flag=True, default=False, help="Emit JSON output.")
@click.pass_obj
def update_cli_command(cli_context: CLIContext, json_output: bool) -> None:
    """Show the recommended CLI upgrade command for this install mode."""
    if json_output:
        cli_context.json_output = True
    exit_with_result(cli_context, run_update_cli(cli_context))


@update_group.command("deploy")
@click.option("--project", default=None, help="Target GCP project id.")
@click.option("--region", default=None, help="Target GCP region.")
@click.option("--service", default=None, help="Cloud Run service name.")
@click.option("--artifact-repo", default=None, help="Artifact Registry repository name.")
@click.option("--sql-instance", default=None, help="Cloud SQL instance name.")
@click.option("--database", default=None, help="Cloud SQL database name.")
@click.option("--bucket", default=None, help="GCS bucket name for managed memory objects.")
@click.option("--min-instances", type=int, default=None, help="Minimum Cloud Run instances.")
@click.option("--max-instances", type=int, default=None, help="Maximum Cloud Run instances.")
@click.option("--concurrency", type=int, default=None, help="Cloud Run request concurrency.")
@click.option("--cpu", default=None, help="Cloud Run CPU setting, for example 1.")
@click.option("--memory", default=None, help="Cloud Run memory setting, for example 1Gi.")
@click.option("--aws-region", default=None, help="Target AWS region.")
@click.option("--aws-service", default=None, help="AWS ECS service name.")
@click.option("--aws-vpc-id", default=None, help="Override VPC id for RDS provisioning.", hidden=True)
@click.option("--aws-subnet-ids", default=None, help="Override subnet ids for RDS provisioning.", hidden=True)
@click.option("--aws-database-url", default=None, help="Existing managed PostgreSQL URL.")
@click.option("--aws-s3-bucket", default=None, help="S3 bucket name for managed memory objects.")
@click.option("--aws-ecr-repo", default=None, help="ECR repository name.")
@click.option("--azure-subscription", default=None, help="Target Azure subscription id.")
@click.option("--azure-resource-group", default=None, help="Target Azure resource group.")
@click.option("--azure-region", default=None, help="Target Azure region.")
@click.option("--azure-environment", default=None, help="Container Apps environment name.")
@click.option("--azure-app", default=None, help="Container App name.")
@click.option("--azure-database-url", default=None, help="Existing managed PostgreSQL URL.")
@click.option("--azure-storage-account", default=None, help="Azure Storage account name.")
@click.option("--azure-blob-container", default=None, help="Azure Blob container name.")
@click.option("--azure-blob-endpoint", default=None, help="Azure Blob endpoint URL.")
@click.option("--azure-acr-server", default=None, help="Azure Container Registry login server.")
@click.option("--azure-acr-repo", default=None, help="ACR repository name.")
@click.option("--cors-origins", default=None, help="Explicit production CORS origins (comma-separated).")
@click.option("--allowed-hosts", default=None, help="Explicit production allowed hosts (comma-separated).")
@click.option("--tag", default=None, help="Container image tag.")
@click.pass_obj
def update_deploy_command(
    cli_context: CLIContext,
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
    aws_database_url: str | None,
    aws_s3_bucket: str | None,
    aws_ecr_repo: str | None,
    azure_subscription: str | None,
    azure_resource_group: str | None,
    azure_region: str | None,
    azure_environment: str | None,
    azure_app: str | None,
    azure_database_url: str | None,
    azure_storage_account: str | None,
    azure_blob_container: str | None,
    azure_blob_endpoint: str | None,
    azure_acr_server: str | None,
    azure_acr_repo: str | None,
    cors_origins: str | None,
    allowed_hosts: str | None,
    tag: str | None,
) -> None:
    """Redeploy the active managed target using the current public deploy path."""
    exit_with_result(
        cli_context,
        run_update_deploy(
            cli_context,
            UpdateDeployOptions(
                project=project,
                region=region,
                service=service,
                artifact_repo=artifact_repo,
                sql_instance=sql_instance,
                database=database,
                bucket=bucket,
                cors_origins=cors_origins,
                allowed_hosts=allowed_hosts,
                tag=tag,
                min_instances=min_instances,
                max_instances=max_instances,
                concurrency=concurrency,
                cpu=cpu,
                memory=memory,
                aws_region=aws_region,
                aws_service=aws_service,
                aws_vpc_id=aws_vpc_id,
                aws_subnet_ids=aws_subnet_ids,
                aws_database_url=aws_database_url,
                aws_s3_bucket=aws_s3_bucket,
                aws_ecr_repo=aws_ecr_repo,
                azure_subscription=azure_subscription,
                azure_resource_group=azure_resource_group,
                azure_region=azure_region,
                azure_environment=azure_environment,
                azure_app=azure_app,
                azure_database_url=azure_database_url,
                azure_storage_account=azure_storage_account,
                azure_blob_container=azure_blob_container,
                azure_blob_endpoint=azure_blob_endpoint,
                azure_acr_server=azure_acr_server,
                azure_acr_repo=azure_acr_repo,
            ),
        ),
    )
