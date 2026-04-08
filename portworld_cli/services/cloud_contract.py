from __future__ import annotations

from dataclasses import dataclass

from portworld_cli.targets import (
    TARGET_AWS_ECS_FARGATE,
    TARGET_AZURE_CONTAINER_APPS,
    TARGET_GCP_CLOUD_RUN,
)

COMMAND_KEY_DOCTOR = "doctor"
COMMAND_KEY_UPDATE_DEPLOY = "update_deploy"

PROVIDER_KEY_GCP = "gcp"
PROVIDER_KEY_AWS = "aws"
PROVIDER_KEY_AZURE = "azure"

_PROVIDER_LABELS: dict[str, str] = {
    PROVIDER_KEY_GCP: "GCP",
    PROVIDER_KEY_AWS: "AWS",
    PROVIDER_KEY_AZURE: "Azure",
}

_DOCTOR_ALLOWED_PROVIDERS_BY_TARGET: dict[str, str | None] = {
    "local": None,
    TARGET_GCP_CLOUD_RUN: PROVIDER_KEY_GCP,
    TARGET_AWS_ECS_FARGATE: PROVIDER_KEY_AWS,
    TARGET_AZURE_CONTAINER_APPS: PROVIDER_KEY_AZURE,
}

_UPDATE_ALLOWED_PROVIDERS_BY_TARGET: dict[str, str | None] = {
    TARGET_GCP_CLOUD_RUN: PROVIDER_KEY_GCP,
    TARGET_AWS_ECS_FARGATE: PROVIDER_KEY_AWS,
    TARGET_AZURE_CONTAINER_APPS: PROVIDER_KEY_AZURE,
}


@dataclass(frozen=True, slots=True)
class GCPCloudOptions:
    project: str | None = None
    region: str | None = None
    service: str | None = None
    artifact_repo: str | None = None
    bucket: str | None = None
    min_instances: int | None = None
    max_instances: int | None = None
    concurrency: int | None = None
    cpu: str | None = None
    memory: str | None = None

    def has_any_values(self) -> bool:
        return any(
            value is not None
            for value in (
                self.project,
                self.region,
                self.service,
                self.artifact_repo,
                self.bucket,
                self.min_instances,
                self.max_instances,
                self.concurrency,
                self.cpu,
                self.memory,
            )
        )


@dataclass(frozen=True, slots=True)
class AWSCloudOptions:
    region: str | None = None
    service: str | None = None
    vpc_id: str | None = None
    subnet_ids: str | None = None
    s3_bucket: str | None = None
    ecr_repo: str | None = None

    def has_any_values(self) -> bool:
        return any(
            value is not None
            for value in (
                self.region,
                self.service,
                self.vpc_id,
                self.subnet_ids,
                self.s3_bucket,
                self.ecr_repo,
            )
        )


@dataclass(frozen=True, slots=True)
class AzureCloudOptions:
    subscription: str | None = None
    resource_group: str | None = None
    region: str | None = None
    environment: str | None = None
    app: str | None = None
    storage_account: str | None = None
    blob_container: str | None = None
    blob_endpoint: str | None = None
    acr_server: str | None = None
    acr_repo: str | None = None

    def has_any_values(self) -> bool:
        return any(
            value is not None
            for value in (
                self.subscription,
                self.resource_group,
                self.region,
                self.environment,
                self.app,
                self.storage_account,
                self.blob_container,
                self.blob_endpoint,
                self.acr_server,
                self.acr_repo,
            )
        )


@dataclass(frozen=True, slots=True)
class CloudProviderOptions:
    gcp: GCPCloudOptions
    aws: AWSCloudOptions
    azure: AzureCloudOptions

    @classmethod
    def empty(cls) -> "CloudProviderOptions":
        return cls(gcp=GCPCloudOptions(), aws=AWSCloudOptions(), azure=AzureCloudOptions())

    def providers_with_values(self) -> tuple[str, ...]:
        values: list[str] = []
        if self.gcp.has_any_values():
            values.append(PROVIDER_KEY_GCP)
        if self.aws.has_any_values():
            values.append(PROVIDER_KEY_AWS)
        if self.azure.has_any_values():
            values.append(PROVIDER_KEY_AZURE)
        return tuple(values)


@dataclass(frozen=True, slots=True)
class GCPDoctorOptions:
    project: str | None
    region: str | None


@dataclass(frozen=True, slots=True)
class AWSDoctorOptions:
    region: str | None
    service: str | None
    vpc_id: str | None
    subnet_ids: str | None
    s3_bucket: str | None


@dataclass(frozen=True, slots=True)
class AzureDoctorOptions:
    subscription: str | None
    resource_group: str | None
    region: str | None
    environment: str | None
    app: str | None
    storage_account: str | None
    blob_container: str | None
    blob_endpoint: str | None


@dataclass(frozen=True, slots=True)
class CloudUsageValidationIssue:
    problem: str
    next_step: str


def validate_cloud_flag_scope_for_doctor(
    *,
    target: str,
    cloud_options: CloudProviderOptions,
) -> CloudUsageValidationIssue | None:
    return _validate_cloud_flag_scope(
        command_key=COMMAND_KEY_DOCTOR,
        target=target,
        cloud_options=cloud_options,
    )


def validate_cloud_flag_scope_for_update_deploy(
    *,
    active_target: str,
    cloud_options: CloudProviderOptions,
) -> CloudUsageValidationIssue | None:
    return _validate_cloud_flag_scope(
        command_key=COMMAND_KEY_UPDATE_DEPLOY,
        target=active_target,
        cloud_options=cloud_options,
    )


def _validate_cloud_flag_scope(
    *,
    command_key: str,
    target: str,
    cloud_options: CloudProviderOptions,
) -> CloudUsageValidationIssue | None:
    if command_key == COMMAND_KEY_DOCTOR:
        allowed = _DOCTOR_ALLOWED_PROVIDERS_BY_TARGET.get(target)
    else:
        allowed = _UPDATE_ALLOWED_PROVIDERS_BY_TARGET.get(target)

    providers_with_values = cloud_options.providers_with_values()
    if not providers_with_values:
        return None

    if allowed is None:
        if command_key == COMMAND_KEY_DOCTOR:
            return CloudUsageValidationIssue(
                problem=(
                    "Cloud target options are only supported with --target gcp-cloud-run, "
                    "--target aws-ecs-fargate, or --target azure-container-apps."
                ),
                next_step="Run `portworld doctor --target local` without cloud flags, or choose a managed target.",
            )
        return CloudUsageValidationIssue(
            problem=(
                "Cloud target options are only supported when the active managed target is "
                "gcp-cloud-run, aws-ecs-fargate, or azure-container-apps."
            ),
            next_step="Set an active managed target with `portworld deploy <target>`, then retry `portworld update deploy`.",
        )

    invalid_providers = tuple(provider for provider in providers_with_values if provider != allowed)
    if not invalid_providers:
        return None

    invalid_labels = "/".join(_PROVIDER_LABELS[provider] for provider in invalid_providers)
    if command_key == COMMAND_KEY_DOCTOR:
        return CloudUsageValidationIssue(
            problem=f"{invalid_labels} flags are not supported with --target {target}.",
            next_step=f"Use only {_PROVIDER_LABELS[allowed]} flags with `--target {target}`, or switch `--target`.",
        )
    return CloudUsageValidationIssue(
        problem=f"{invalid_labels} flags are not supported when the active managed target is {target}.",
        next_step=f"Use only {_PROVIDER_LABELS[allowed]} flags for `portworld update deploy`, or deploy another target directly.",
    )


def to_gcp_doctor_options(cloud_options: CloudProviderOptions) -> GCPDoctorOptions:
    return GCPDoctorOptions(
        project=cloud_options.gcp.project,
        region=cloud_options.gcp.region,
    )


def to_aws_doctor_options(cloud_options: CloudProviderOptions) -> AWSDoctorOptions:
    return AWSDoctorOptions(
        region=cloud_options.aws.region,
        service=cloud_options.aws.service,
        vpc_id=cloud_options.aws.vpc_id,
        subnet_ids=cloud_options.aws.subnet_ids,
        s3_bucket=cloud_options.aws.s3_bucket,
    )


def to_azure_doctor_options(cloud_options: CloudProviderOptions) -> AzureDoctorOptions:
    return AzureDoctorOptions(
        subscription=cloud_options.azure.subscription,
        resource_group=cloud_options.azure.resource_group,
        region=cloud_options.azure.region,
        environment=cloud_options.azure.environment,
        app=cloud_options.azure.app,
        storage_account=cloud_options.azure.storage_account,
        blob_container=cloud_options.azure.blob_container,
        blob_endpoint=cloud_options.azure.blob_endpoint,
    )


def to_gcp_deploy_options(cloud_options: CloudProviderOptions, *, tag: str | None):
    from portworld_cli.deploy.config import DeployGCPCloudRunOptions

    gcp = cloud_options.gcp
    return DeployGCPCloudRunOptions(
        project=gcp.project,
        region=gcp.region,
        service=gcp.service,
        artifact_repo=gcp.artifact_repo,
        bucket=gcp.bucket,
        tag=tag,
        min_instances=gcp.min_instances,
        max_instances=gcp.max_instances,
        concurrency=gcp.concurrency,
        cpu=gcp.cpu,
        memory=gcp.memory,
    )


def to_aws_deploy_options(cloud_options: CloudProviderOptions, *, tag: str | None):
    from portworld_cli.aws.deploy import DeployAWSECSFargateOptions

    aws = cloud_options.aws
    return DeployAWSECSFargateOptions(
        region=aws.region,
        service=aws.service,
        vpc_id=aws.vpc_id,
        subnet_ids=aws.subnet_ids,
        bucket=aws.s3_bucket,
        ecr_repo=aws.ecr_repo,
        tag=tag,
    )


def to_azure_deploy_options(cloud_options: CloudProviderOptions, *, tag: str | None):
    from portworld_cli.azure.deploy import DeployAzureContainerAppsOptions

    azure = cloud_options.azure
    return DeployAzureContainerAppsOptions(
        subscription=azure.subscription,
        resource_group=azure.resource_group,
        region=azure.region,
        environment=azure.environment,
        app=azure.app,
        storage_account=azure.storage_account,
        blob_container=azure.blob_container,
        blob_endpoint=azure.blob_endpoint,
        acr_server=azure.acr_server,
        acr_repo=azure.acr_repo,
        tag=tag,
    )


def problem_next_message(*, problem: str, next_step: str, stage: str | None = None) -> str:
    lines: list[str] = []
    if stage:
        lines.append(f"stage: {stage}")
    lines.append(f"problem: {problem}")
    lines.append(f"next: {next_step}")
    return "\n".join(lines)
