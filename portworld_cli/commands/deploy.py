from __future__ import annotations

import click

from portworld_cli.aws.deploy import (
    DeployAWSECSFargateOptions,
    run_deploy_aws_ecs_fargate,
)
from portworld_cli.azure.deploy import (
    DeployAzureContainerAppsOptions,
    run_deploy_azure_container_apps,
)
from portworld_cli.context import CLIContext
from portworld_cli.deploy.config import (
    DeployGCPCloudRunOptions,
)
from portworld_cli.deploy.service import run_deploy_gcp_cloud_run
from portworld_cli.output import exit_with_result


@click.group("deploy")
def deploy_group() -> None:
    """Deploy PortWorld to a managed target."""


@deploy_group.command("gcp-cloud-run")
@click.option("--project", default=None, help="Target GCP project id.")
@click.option("--region", default=None, help="Target GCP region.")
@click.option("--service", default=None, help="Cloud Run service name.")
@click.option("--artifact-repo", default=None, help="Artifact Registry repository name.")
@click.option("--sql-instance", default=None, help="Cloud SQL instance name.")
@click.option("--database", default=None, help="Cloud SQL database name.")
@click.option("--bucket", default=None, help="GCS bucket name for managed artifacts.")
@click.option("--cors-origins", default=None, help="Explicit production CORS origins (comma-separated).")
@click.option("--allowed-hosts", default=None, help="Explicit production allowed hosts (comma-separated).")
@click.option("--tag", default=None, help="Container image tag.")
@click.option("--min-instances", type=int, default=None, help="Minimum Cloud Run instances.")
@click.option("--max-instances", type=int, default=None, help="Maximum Cloud Run instances.")
@click.option("--concurrency", type=int, default=None, help="Cloud Run request concurrency.")
@click.option("--cpu", default=None, help="Cloud Run CPU setting, for example 1.")
@click.option("--memory", default=None, help="Cloud Run memory setting, for example 1Gi.")
@click.pass_obj
def deploy_gcp_cloud_run_command(
    cli_context: CLIContext,
    project: str | None,
    region: str | None,
    service: str | None,
    artifact_repo: str | None,
    sql_instance: str | None,
    database: str | None,
    bucket: str | None,
    cors_origins: str | None,
    allowed_hosts: str | None,
    tag: str | None,
    min_instances: int | None,
    max_instances: int | None,
    concurrency: int | None,
    cpu: str | None,
    memory: str | None,
) -> None:
    """Deploy PortWorld backend to GCP Cloud Run."""
    exit_with_result(
        cli_context,
        run_deploy_gcp_cloud_run(
            cli_context,
            DeployGCPCloudRunOptions(
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
            ),
        ),
    )


@deploy_group.command("aws-ecs-fargate")
@click.option("--region", default=None, help="Target AWS region.")
@click.option("--cluster", default=None, help="ECS cluster name.")
@click.option("--service", default=None, help="ECS service name.")
@click.option("--vpc-id", default=None, help="VPC id.")
@click.option("--subnet-ids", default=None, help="Subnet ids (comma-separated).")
@click.option("--certificate-arn", default=None, help="ACM certificate ARN for HTTPS listener.")
@click.option("--database-url", default=None, help="Existing managed PostgreSQL URL.")
@click.option("--bucket", default=None, help="S3 bucket name for managed artifacts.")
@click.option("--alb-url", default=None, help="HTTPS ALB endpoint URL.")
@click.option("--ecr-repo", default=None, help="ECR repository name.")
@click.option("--tag", default=None, help="Container image tag.")
@click.option("--cors-origins", default=None, help="Explicit production CORS origins (comma-separated).")
@click.option("--allowed-hosts", default=None, help="Explicit production allowed hosts (comma-separated).")
@click.pass_obj
def deploy_aws_ecs_fargate_command(
    cli_context: CLIContext,
    region: str | None,
    cluster: str | None,
    service: str | None,
    vpc_id: str | None,
    subnet_ids: str | None,
    certificate_arn: str | None,
    database_url: str | None,
    bucket: str | None,
    alb_url: str | None,
    ecr_repo: str | None,
    tag: str | None,
    cors_origins: str | None,
    allowed_hosts: str | None,
) -> None:
    """Deploy PortWorld backend to AWS ECS/Fargate."""
    exit_with_result(
        cli_context,
        run_deploy_aws_ecs_fargate(
            cli_context,
            DeployAWSECSFargateOptions(
                region=region,
                cluster=cluster,
                service=service,
                vpc_id=vpc_id,
                subnet_ids=subnet_ids,
                certificate_arn=certificate_arn,
                database_url=database_url,
                bucket=bucket,
                alb_url=alb_url,
                ecr_repo=ecr_repo,
                tag=tag,
                cors_origins=cors_origins,
                allowed_hosts=allowed_hosts,
            ),
        ),
    )


@deploy_group.command("azure-container-apps")
@click.option("--subscription", default=None, help="Target Azure subscription id.")
@click.option("--resource-group", default=None, help="Target Azure resource group.")
@click.option("--region", default=None, help="Target Azure region.")
@click.option("--environment", default=None, help="Container Apps environment name.")
@click.option("--app", default=None, help="Container App name.")
@click.option("--database-url", default=None, help="Existing managed PostgreSQL URL.")
@click.option("--storage-account", default=None, help="Azure Storage account name.")
@click.option("--blob-container", default=None, help="Azure Blob container name.")
@click.option("--blob-endpoint", default=None, help="Azure Blob endpoint URL.")
@click.option("--acr-server", default=None, help="Azure Container Registry login server.")
@click.option("--acr-repo", default=None, help="ACR repository name.")
@click.option("--tag", default=None, help="Container image tag.")
@click.option("--cors-origins", default=None, help="Explicit production CORS origins (comma-separated).")
@click.option("--allowed-hosts", default=None, help="Explicit production allowed hosts (comma-separated).")
@click.pass_obj
def deploy_azure_container_apps_command(
    cli_context: CLIContext,
    subscription: str | None,
    resource_group: str | None,
    region: str | None,
    environment: str | None,
    app: str | None,
    database_url: str | None,
    storage_account: str | None,
    blob_container: str | None,
    blob_endpoint: str | None,
    acr_server: str | None,
    acr_repo: str | None,
    tag: str | None,
    cors_origins: str | None,
    allowed_hosts: str | None,
) -> None:
    """Deploy PortWorld backend to Azure Container Apps."""
    exit_with_result(
        cli_context,
        run_deploy_azure_container_apps(
            cli_context,
            DeployAzureContainerAppsOptions(
                subscription=subscription,
                resource_group=resource_group,
                region=region,
                environment=environment,
                app=app,
                database_url=database_url,
                storage_account=storage_account,
                blob_container=blob_container,
                blob_endpoint=blob_endpoint,
                acr_server=acr_server,
                acr_repo=acr_repo,
                tag=tag,
                cors_origins=cors_origins,
                allowed_hosts=allowed_hosts,
            ),
        ),
    )
