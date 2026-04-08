from __future__ import annotations

import click

from portworld_cli.context import CLIContext
from portworld_cli.output import exit_with_result
from portworld_cli.services.cloud_contract import AWSCloudOptions, AzureCloudOptions, CloudProviderOptions, GCPCloudOptions
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
@click.option("--gcp-project", default=None, help="Target GCP project id.")
@click.option("--gcp-region", default=None, help="Target GCP region.")
@click.option("--gcp-service", default=None, help="Cloud Run service name.")
@click.option("--gcp-artifact-repo", default=None, help="Artifact Registry repository name.")
@click.option("--gcp-bucket", default=None, help="GCS bucket name for managed memory objects.")
@click.option("--gcp-min-instances", type=int, default=None, help="Minimum Cloud Run instances.")
@click.option("--gcp-max-instances", type=int, default=None, help="Maximum Cloud Run instances.")
@click.option("--gcp-concurrency", type=int, default=None, help="Cloud Run request concurrency.")
@click.option("--gcp-cpu", default=None, help="Cloud Run CPU setting, for example 1.")
@click.option("--gcp-memory", default=None, help="Cloud Run memory setting, for example 1Gi.")
@click.option("--aws-region", default=None, help="Target AWS region.")
@click.option("--aws-service", default=None, help="AWS ECS service name.")
@click.option("--aws-vpc-id", default=None, help="Override VPC id for managed networking.", hidden=True)
@click.option("--aws-subnet-ids", default=None, help="Override subnet ids for managed networking.", hidden=True)
@click.option("--aws-s3-bucket", default=None, help="S3 bucket name for managed memory objects.")
@click.option("--aws-ecr-repo", default=None, help="ECR repository name.")
@click.option("--azure-subscription", default=None, help="Target Azure subscription id.")
@click.option("--azure-resource-group", default=None, help="Target Azure resource group.")
@click.option("--azure-region", default=None, help="Target Azure region.")
@click.option("--azure-environment", default=None, help="Container Apps environment name.")
@click.option("--azure-app", default=None, help="Container App name.")
@click.option("--azure-storage-account", default=None, help="Azure Storage account name.")
@click.option("--azure-blob-container", default=None, help="Azure Blob container name.")
@click.option("--azure-blob-endpoint", default=None, help="Azure Blob endpoint URL.")
@click.option("--azure-acr-server", default=None, help="Azure Container Registry login server.")
@click.option("--azure-acr-repo", default=None, help="ACR repository name.")
@click.option("--tag", default=None, help="Container image tag.")
@click.pass_obj
def update_deploy_command(
    cli_context: CLIContext,
    gcp_project: str | None,
    gcp_region: str | None,
    gcp_service: str | None,
    gcp_artifact_repo: str | None,
    gcp_bucket: str | None,
    gcp_min_instances: int | None,
    gcp_max_instances: int | None,
    gcp_concurrency: int | None,
    gcp_cpu: str | None,
    gcp_memory: str | None,
    aws_region: str | None,
    aws_service: str | None,
    aws_vpc_id: str | None,
    aws_subnet_ids: str | None,
    aws_s3_bucket: str | None,
    aws_ecr_repo: str | None,
    azure_subscription: str | None,
    azure_resource_group: str | None,
    azure_region: str | None,
    azure_environment: str | None,
    azure_app: str | None,
    azure_storage_account: str | None,
    azure_blob_container: str | None,
    azure_blob_endpoint: str | None,
    azure_acr_server: str | None,
    azure_acr_repo: str | None,
    tag: str | None,
) -> None:
    """Redeploy the active managed target using the current public deploy path."""
    exit_with_result(
        cli_context,
        run_update_deploy(
            cli_context,
            UpdateDeployOptions(
                tag=tag,
                cloud=CloudProviderOptions(
                    gcp=GCPCloudOptions(
                        project=gcp_project,
                        region=gcp_region,
                        service=gcp_service,
                        artifact_repo=gcp_artifact_repo,
                        bucket=gcp_bucket,
                        min_instances=gcp_min_instances,
                        max_instances=gcp_max_instances,
                        concurrency=gcp_concurrency,
                        cpu=gcp_cpu,
                        memory=gcp_memory,
                    ),
                    aws=AWSCloudOptions(
                        region=aws_region,
                        service=aws_service,
                        vpc_id=aws_vpc_id,
                        subnet_ids=aws_subnet_ids,
                        s3_bucket=aws_s3_bucket,
                        ecr_repo=aws_ecr_repo,
                    ),
                    azure=AzureCloudOptions(
                        subscription=azure_subscription,
                        resource_group=azure_resource_group,
                        region=azure_region,
                        environment=azure_environment,
                        app=azure_app,
                        storage_account=azure_storage_account,
                        blob_container=azure_blob_container,
                        blob_endpoint=azure_blob_endpoint,
                        acr_server=azure_acr_server,
                        acr_repo=azure_acr_repo,
                    ),
                ),
            ),
        ),
    )
