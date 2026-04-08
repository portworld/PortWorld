from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from portworld_cli.aws.common import normalize_optional_text, run_aws_json, split_csv_values, validate_s3_bucket_name
from portworld_cli.aws.stages.shared import now_ms, read_dict_string
from portworld_cli.context import CLIContext
from portworld_cli.deploy.config import DeployStageError, DeployUsageError
from portworld_cli.deploy.published import resolve_published_image_selection
from portworld_cli.deploy.source import resolve_source_image_tag
from portworld_cli.deploy_artifacts import IMAGE_SOURCE_MODE_SOURCE_BUILD
from portworld_cli.ux.prompts import prompt_text
from portworld_cli.workspace.project_config import RUNTIME_SOURCE_PUBLISHED


@dataclass(frozen=True, slots=True)
class DeployAWSECSFargateOptions:
    region: str | None = None
    service: str | None = None
    vpc_id: str | None = None
    subnet_ids: str | None = None
    bucket: str | None = None
    ecr_repo: str | None = None
    tag: str | None = None
    database_url: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedAWSDeployConfig:
    runtime_source: str
    image_source_mode: str
    account_id: str
    region: str
    app_name: str
    requested_vpc_id: str | None
    requested_subnet_ids: tuple[str, ...]
    bucket_name: str
    ecr_repository: str | None
    image_tag: str
    image_uri: str
    published_release_tag: str | None
    published_image_ref: str | None


def resolve_aws_deploy_config(
    cli_context: CLIContext,
    *,
    options: DeployAWSECSFargateOptions,
    env_values: OrderedDict[str, str],
    project_config,
    runtime_source: str,
    project_root: Path | None,
) -> ResolvedAWSDeployConfig:
    aws_defaults = project_config.deploy.aws_ecs_fargate

    region = require_value(
        cli_context,
        value=first_non_empty(options.region, aws_defaults.region),
        prompt="AWS region",
        error="AWS region is required.",
    )
    app_name = require_value(
        cli_context,
        value=first_non_empty(
            options.service,
            aws_defaults.service_name,
        ),
        prompt="AWS ECS service name",
        error="AWS service name is required (--service).",
    )

    requested_vpc_id = first_non_empty(options.vpc_id, aws_defaults.vpc_id)
    requested_subnet_ids = split_csv_values(options.subnet_ids) or tuple(aws_defaults.subnet_ids)

    bucket_name = first_non_empty(
        options.bucket,
        env_values.get("BACKEND_OBJECT_STORE_NAME"),
        f"{app_name}-memory",
    )
    assert bucket_name is not None
    bucket_error = validate_s3_bucket_name(bucket_name)
    if bucket_error:
        raise DeployUsageError(bucket_error)

    identity = run_aws_json(["sts", "get-caller-identity"])
    if not identity.ok or not isinstance(identity.value, dict):
        raise DeployStageError(
            stage="prerequisite_validation",
            message=identity.message or "Unable to resolve AWS caller identity.",
            action="Run `aws configure` and ensure sts:GetCallerIdentity succeeds.",
        )
    account_id = read_dict_string(identity.value, "Account")
    if account_id is None:
        raise DeployStageError(
            stage="prerequisite_validation",
            message="AWS caller identity did not include an account id.",
            action="Verify AWS credentials and retry.",
        )

    image_source_mode = IMAGE_SOURCE_MODE_SOURCE_BUILD
    published_release_tag: str | None = None
    published_image_ref: str | None = None
    ecr_repository: str | None = None
    if runtime_source == RUNTIME_SOURCE_PUBLISHED:
        if options.ecr_repo is not None:
            raise DeployUsageError("--ecr-repo is only supported in runtime_source=source.")
        published = resolve_published_image_selection(
            explicit_tag=options.tag,
            artifact_repository=f"{app_name}-backend",
            release_tag=project_config.deploy.published_runtime.release_tag,
            image_ref=project_config.deploy.published_runtime.image_ref,
        )
        image_source_mode = published.image_source_mode
        image_tag = published.image_tag
        published_release_tag = published.release_tag
        published_image_ref = published.image_ref
        image_uri = published.image_ref
    else:
        ecr_repository = first_non_empty(options.ecr_repo, f"{app_name}-backend")
        assert ecr_repository is not None
        if project_root is None:
            image_tag = normalize_optional_text(options.tag) or str(now_ms())
        else:
            image_tag = resolve_source_image_tag(explicit_tag=options.tag, project_root=project_root)
        image_uri = (
            f"{account_id}.dkr.ecr.{region}.amazonaws.com/"
            f"{ecr_repository}:{image_tag}"
        )

    return ResolvedAWSDeployConfig(
        runtime_source=runtime_source,
        image_source_mode=image_source_mode,
        account_id=account_id,
        region=region,
        app_name=app_name,
        requested_vpc_id=requested_vpc_id,
        requested_subnet_ids=requested_subnet_ids,
        bucket_name=bucket_name,
        ecr_repository=ecr_repository,
        image_tag=image_tag,
        image_uri=image_uri,
        published_release_tag=published_release_tag,
        published_image_ref=published_image_ref,
    )


def require_value(cli_context: CLIContext, *, value: str | None, prompt: str, error: str) -> str:
    normalized = normalize_optional_text(value)
    if normalized is not None:
        return normalized
    if cli_context.non_interactive:
        raise DeployUsageError(error)
    prompted = prompt_text(
        cli_context,
        message=prompt,
        default="",
        show_default=False,
    )
    normalized = normalize_optional_text(prompted)
    if normalized is None:
        raise DeployUsageError(error)
    return normalized


def first_non_empty(*values: str | None) -> str | None:
    for value in values:
        normalized = normalize_optional_text(value)
        if normalized is not None:
            return normalized
    return None
