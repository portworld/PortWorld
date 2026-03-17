from __future__ import annotations

import click

from portworld_cli.context import CLIContext
from portworld_cli.output import exit_with_result
from portworld_cli.services.doctor import DoctorOptions, run_doctor


@click.command("doctor")
@click.option(
    "--target",
    type=click.Choice(["local", "gcp-cloud-run", "aws-ecs-fargate", "azure-container-apps"]),
    default="local",
    show_default=True,
    help="Readiness target to validate.",
)
@click.option(
    "--full",
    is_flag=True,
    default=False,
    help="Run the storage bootstrap probe in addition to standard local checks.",
)
@click.option("--project", default=None, help="Target GCP project id for future gcp-cloud-run checks.")
@click.option("--region", default=None, help="Target GCP region for future gcp-cloud-run checks.")
@click.option("--aws-region", default=None, help="Target AWS region for aws-ecs-fargate checks.")
@click.option("--aws-cluster", default=None, help="Target ECS cluster name.")
@click.option("--aws-service", default=None, help="Target ECS service name.")
@click.option("--aws-vpc-id", default=None, help="Target VPC id.")
@click.option("--aws-subnet-ids", default=None, help="Target subnet ids (comma-separated).")
@click.option("--aws-certificate-arn", default=None, help="ACM certificate ARN for HTTPS listener.")
@click.option("--aws-database-url", default=None, help="Existing managed Postgres URL.")
@click.option("--aws-s3-bucket", default=None, help="S3 bucket name for managed artifacts.")
@click.option("--azure-subscription", default=None, help="Target Azure subscription id.")
@click.option("--azure-resource-group", default=None, help="Target Azure resource group.")
@click.option("--azure-region", default=None, help="Target Azure region.")
@click.option("--azure-environment", default=None, help="Target Container Apps environment name.")
@click.option("--azure-app", default=None, help="Target Container App name.")
@click.option("--azure-database-url", default=None, help="Existing managed Postgres URL.")
@click.option("--azure-storage-account", default=None, help="Target Azure storage account name.")
@click.option("--azure-blob-container", default=None, help="Target Azure blob container name.")
@click.option("--azure-blob-endpoint", default=None, help="Target Azure blob endpoint URL.")
@click.pass_obj
def doctor_command(
    cli_context: CLIContext,
    target: str,
    full: bool,
    project: str | None,
    region: str | None,
    aws_region: str | None,
    aws_cluster: str | None,
    aws_service: str | None,
    aws_vpc_id: str | None,
    aws_subnet_ids: str | None,
    aws_certificate_arn: str | None,
    aws_database_url: str | None,
    aws_s3_bucket: str | None,
    azure_subscription: str | None,
    azure_resource_group: str | None,
    azure_region: str | None,
    azure_environment: str | None,
    azure_app: str | None,
    azure_database_url: str | None,
    azure_storage_account: str | None,
    azure_blob_container: str | None,
    azure_blob_endpoint: str | None,
) -> None:
    """Validate local or managed deployment readiness."""
    exit_with_result(
        cli_context,
        run_doctor(
            cli_context,
            DoctorOptions(
                target=target,
                full=full,
                project=project,
                region=region,
                aws_region=aws_region,
                aws_cluster=aws_cluster,
                aws_service=aws_service,
                aws_vpc_id=aws_vpc_id,
                aws_subnet_ids=aws_subnet_ids,
                aws_certificate_arn=aws_certificate_arn,
                aws_database_url=aws_database_url,
                aws_s3_bucket=aws_s3_bucket,
                azure_subscription=azure_subscription,
                azure_resource_group=azure_resource_group,
                azure_region=azure_region,
                azure_environment=azure_environment,
                azure_app=azure_app,
                azure_database_url=azure_database_url,
                azure_storage_account=azure_storage_account,
                azure_blob_container=azure_blob_container,
                azure_blob_endpoint=azure_blob_endpoint,
            ),
        ),
    )
