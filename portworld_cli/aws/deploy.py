from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
import json
import secrets
import socket
import ssl
import string
import subprocess
import time
from time import monotonic
from time import time_ns
from urllib.parse import quote, urlparse

import click
import httpx

from portworld_cli.aws.common import (
    aws_cli_available,
    is_postgres_url,
    normalize_optional_text,
    run_aws_json,
    run_aws_text,
    split_csv_values,
    validate_s3_bucket_name,
)
from portworld_cli.context import CLIContext
from portworld_cli.deploy.config import DeployStageError, DeployUsageError, load_deploy_session
from portworld_cli.deploy.published import resolve_published_image_selection
from portworld_cli.deploy.source import resolve_source_image_tag
from portworld_cli.deploy_artifacts import IMAGE_SOURCE_MODE_SOURCE_BUILD
from portworld_cli.deploy_state import DeployState, write_deploy_state
from portworld_cli.output import CommandResult
from portworld_cli.targets import TARGET_AWS_ECS_FARGATE
from portworld_cli.workspace.project_config import RUNTIME_SOURCE_PUBLISHED

COMMAND_NAME = "portworld deploy aws-ecs-fargate"
RDS_INSTANCE_CLASS = "db.t3.micro"
RDS_STORAGE_GB = "20"
ECS_EXECUTION_ROLE_NAME = "portworld-ecs-task-execution"
ECS_TASK_ROLE_SUFFIX = "ecs-task-runtime"
ECS_TASK_INLINE_POLICY_NAME = "portworld-ecs-task-runtime-s3"
ECS_SERVICE_LINKED_ROLE_NAME = "AWSServiceRoleForECS"
ECS_TASK_CPU = "1024"
ECS_TASK_MEMORY = "2048"
RDS_PASSWORD_PARAM_PREFIX = "/portworld"
MANAGED_CACHE_POLICY_CACHING_DISABLED = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
MANAGED_ORIGIN_REQUEST_POLICY_ALL_VIEWER = "216adef6-5c7f-47e4-b989-5492eafa07d3"


@dataclass(frozen=True, slots=True)
class DeployAWSECSFargateOptions:
    region: str | None
    cluster: str | None
    service: str | None
    vpc_id: str | None
    subnet_ids: str | None
    database_url: str | None
    bucket: str | None
    ecr_repo: str | None
    tag: str | None
    cors_origins: str | None
    allowed_hosts: str | None


@dataclass(frozen=True, slots=True)
class _ResolvedAWSDeployConfig:
    runtime_source: str
    image_source_mode: str
    account_id: str
    region: str
    app_name: str
    requested_vpc_id: str | None
    requested_subnet_ids: tuple[str, ...]
    explicit_database_url: str | None
    bucket_name: str
    ecr_repository: str
    image_tag: str
    image_uri: str
    cors_origins: str
    allowed_hosts: str
    rds_instance_identifier: str
    rds_db_name: str
    rds_master_username: str
    rds_password_parameter_name: str
    published_release_tag: str | None
    published_image_ref: str | None


@dataclass(frozen=True, slots=True)
class _AWSDeployMutationResult:
    database_url: str
    ecs_cluster_name: str
    ecs_service_name: str
    task_definition_arn: str
    alb_dns_name: str
    cloudfront_distribution_id: str
    cloudfront_domain_name: str
    service_url: str
    resolved_vpc_id: str | None
    resolved_subnet_ids: tuple[str, ...]
    alb_security_group_id: str | None
    ecs_security_group_id: str | None
    rds_security_group_id: str | None
    used_external_database: bool


def run_deploy_aws_ecs_fargate(
    cli_context: CLIContext,
    options: DeployAWSECSFargateOptions,
) -> CommandResult:
    stage_records: list[dict[str, object]] = []
    resources: dict[str, object] = {}
    try:
        session = load_deploy_session(cli_context)
        if not aws_cli_available():
            raise DeployStageError(
                stage="prerequisite_validation",
                message="aws CLI is not installed or not on PATH.",
                action="Install AWS CLI v2 and re-run deploy.",
            )

        env_values = OrderedDict(session.merged_env_values().items())
        config = _resolve_aws_deploy_config(
            cli_context,
            options=options,
            env_values=env_values,
            project_config=session.project_config,
            runtime_source=session.effective_runtime_source,
            project_root=(None if session.project_paths is None else session.project_paths.project_root),
        )

        _confirm_mutations(cli_context, config)
        stage_records.append(_stage_ok("mutation_plan", "Confirmed deploy mutations."))

        resources.update(
            {
                "account_id": config.account_id,
                "region": config.region,
                "ecs_service_name": config.app_name,
                "bucket_name": config.bucket_name,
                "ecr_repository": config.ecr_repository,
                "image_uri": config.image_uri,
                "rds_instance_identifier": config.rds_instance_identifier,
            }
        )

        result = _run_aws_deploy_mutations(
            config,
            env_values=env_values,
            stage_records=stage_records,
            project_root=session.workspace_root,
        )
        resources["ecs_cluster_name"] = result.ecs_cluster_name
        resources["ecs_service_name"] = result.ecs_service_name
        resources["task_definition_arn"] = result.task_definition_arn
        resources["alb_dns_name"] = result.alb_dns_name
        resources["cloudfront_distribution_id"] = result.cloudfront_distribution_id
        resources["cloudfront_domain_name"] = result.cloudfront_domain_name
        resources["service_url"] = result.service_url
        resources["resolved_vpc_id"] = result.resolved_vpc_id
        resources["resolved_subnet_ids"] = list(result.resolved_subnet_ids)
        resources["alb_security_group_id"] = result.alb_security_group_id
        resources["ecs_security_group_id"] = result.ecs_security_group_id
        resources["rds_security_group_id"] = result.rds_security_group_id
        resources["database_url_source"] = "external" if result.used_external_database else "provisioned"

        livez_ok, ws_ok = _wait_for_public_validation(
            result.service_url,
            env_values.get("BACKEND_BEARER_TOKEN", ""),
        )
        if not livez_ok:
            raise DeployStageError(
                stage="post_deploy_validation",
                message="CloudFront public URL did not return 200 from /livez.",
                action="Inspect ECS service, ALB, and CloudFront logs and verify backend startup readiness.",
            )
        if not ws_ok:
            stage_records.append(
                {
                    "stage": "post_deploy_validation",
                    "status": "warn",
                    "message": (
                        "Validated /livez, but /ws/session did not complete a websocket handshake before timeout. "
                        "AWS edge propagation may still be catching up; verify shortly after deploy with `portworld logs aws-ecs-fargate`."
                    ),
                }
            )
        else:
            stage_records.append(_stage_ok("post_deploy_validation", "Validated /livez and /ws/session endpoint reachability."))

        try:
            write_deploy_state(
                session.workspace_paths.state_file_for_target(TARGET_AWS_ECS_FARGATE),
                DeployState(
                    project_id=config.account_id,
                    region=config.region,
                    service_name=config.app_name,
                    runtime_source=config.runtime_source,
                    image_source_mode=config.image_source_mode,
                    artifact_repository=config.ecr_repository,
                    artifact_repository_base=config.ecr_repository,
                    cloud_sql_instance=None,
                    database_name=(config.rds_db_name if not result.used_external_database else "external"),
                    bucket_name=config.bucket_name,
                    image=config.image_uri,
                    published_release_tag=config.published_release_tag,
                    published_image_ref=config.published_image_ref,
                    service_url=result.service_url,
                    service_account_email=None,
                    last_deployed_at_ms=_now_ms(),
                ),
            )
        except Exception as exc:
            raise DeployStageError(
                stage="state_write",
                message=f"Unable to write AWS deploy state: {exc}",
                action="Check workspace permissions for `.portworld/state` and retry.",
            ) from exc
        stage_records.append(_stage_ok("state_write", "Wrote AWS deploy state."))

        runtime_env = _build_runtime_env_vars(env_values, config, database_url=result.database_url)
        message_lines = [
            f"target: {TARGET_AWS_ECS_FARGATE}",
            f"account_id: {config.account_id}",
            f"region: {config.region}",
            f"ecs_cluster: {result.ecs_cluster_name}",
            f"ecs_service: {result.ecs_service_name}",
            f"cloudfront_domain: {result.cloudfront_domain_name}",
            f"alb_dns_name: {result.alb_dns_name}",
            f"service_url: {result.service_url}",
            f"image_source_mode: {config.image_source_mode}",
            f"image_uri: {config.image_uri}",
            f"bucket_name: {config.bucket_name}",
            f"database_url_source: {'external' if result.used_external_database else 'provisioned_rds'}",
            f"websocket_validation: {'validated' if ws_ok else 'timed_out_warn_only'}",
            "next_steps:",
            f"- curl {result.service_url.rstrip('/')}/livez",
            f"- portworld logs aws-ecs-fargate --region {config.region} --service {result.ecs_service_name}",
            f"- portworld doctor --target aws-ecs-fargate --aws-region {config.region}",
        ]
        return CommandResult(
            ok=True,
            command=COMMAND_NAME,
            message="\n".join(message_lines),
            data={
                "target": TARGET_AWS_ECS_FARGATE,
                "region": config.region,
                "ecs_cluster_name": result.ecs_cluster_name,
                "ecs_service_name": result.ecs_service_name,
                "service_url": result.service_url,
                "image": config.image_uri,
                "runtime_source": config.runtime_source,
                "image_source_mode": config.image_source_mode,
                "websocket_validation": "validated" if ws_ok else "timed_out_warn_only",
                "published_release_tag": config.published_release_tag,
                "published_image_ref": config.published_image_ref,
                "resources": resources,
                "stages": stage_records,
                "runtime_env": _sanitize_runtime_env_for_output(runtime_env),
            },
            exit_code=0,
        )
    except (DeployUsageError,) as exc:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message=str(exc),
            data={"error_type": type(exc).__name__, "resources": resources, "stages": stage_records},
            exit_code=2,
        )
    except DeployStageError as exc:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message=f"stage: {exc.stage}\nerror: {exc}",
            data={"stage": exc.stage, "error_type": type(exc).__name__, "resources": resources, "stages": stage_records},
            exit_code=1,
        )
    except click.Abort:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message="Aborted before deploy completed.",
            data={"error_type": "Abort", "resources": resources, "stages": stage_records},
            exit_code=1,
        )
def _resolve_aws_deploy_config(
    cli_context: CLIContext,
    *,
    options: DeployAWSECSFargateOptions,
    env_values: OrderedDict[str, str],
    project_config,
    runtime_source: str,
    project_root: Path | None,
) -> _ResolvedAWSDeployConfig:
    aws_defaults = project_config.deploy.aws_ecs_fargate

    region = _require_value(
        cli_context,
        value=_first_non_empty(options.region, aws_defaults.region),
        prompt="AWS region",
        error="AWS region is required.",
    )
    app_name = _require_value(
        cli_context,
        value=_first_non_empty(
            options.service,
            aws_defaults.service_name,
            options.cluster,
            aws_defaults.cluster_name,
        ),
        prompt="AWS ECS service name",
        error="AWS service name is required (--service or --cluster).",
    )

    requested_vpc_id = _first_non_empty(options.vpc_id, aws_defaults.vpc_id)
    requested_subnet_ids = split_csv_values(options.subnet_ids) or tuple(aws_defaults.subnet_ids)

    explicit_database_url = _first_non_empty(options.database_url, env_values.get("BACKEND_DATABASE_URL"))
    if explicit_database_url and not is_postgres_url(explicit_database_url):
        raise DeployUsageError("BACKEND_DATABASE_URL must use postgres:// or postgresql://.")

    bucket_name = _first_non_empty(
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
    account_id = _read_dict_string(identity.value, "Account")
    if account_id is None:
        raise DeployStageError(
            stage="prerequisite_validation",
            message="AWS caller identity did not include an account id.",
            action="Verify AWS credentials and retry.",
        )

    ecr_repository = _first_non_empty(options.ecr_repo, f"{app_name}-backend")
    assert ecr_repository is not None

    image_source_mode = IMAGE_SOURCE_MODE_SOURCE_BUILD
    published_release_tag: str | None = None
    published_image_ref: str | None = None
    if runtime_source == RUNTIME_SOURCE_PUBLISHED:
        published = resolve_published_image_selection(
            explicit_tag=options.tag,
            artifact_repository=ecr_repository,
            release_tag=project_config.deploy.published_runtime.release_tag,
            image_ref=project_config.deploy.published_runtime.image_ref,
        )
        image_source_mode = published.image_source_mode
        image_tag = published.image_tag
        published_release_tag = published.release_tag
        published_image_ref = published.image_ref
    else:
        if project_root is None:
            image_tag = normalize_optional_text(options.tag) or str(_now_ms())
        else:
            image_tag = resolve_source_image_tag(explicit_tag=options.tag, project_root=project_root)

    image_uri = (
        f"{account_id}.dkr.ecr.{region}.amazonaws.com/"
        f"{ecr_repository}:{image_tag}"
    )

    cors_origins = _first_non_empty(options.cors_origins, env_values.get("CORS_ORIGINS"), "*")
    allowed_hosts = _first_non_empty(options.allowed_hosts, env_values.get("BACKEND_ALLOWED_HOSTS"), "*")
    rds_instance_identifier = _normalize_rds_identifier(f"{app_name}-pg")
    rds_db_name = "portworld"
    rds_master_username = "portworld"
    rds_password_parameter_name = f"{RDS_PASSWORD_PARAM_PREFIX}/{app_name}/rds-master-password"

    return _ResolvedAWSDeployConfig(
        runtime_source=runtime_source,
        image_source_mode=image_source_mode,
        account_id=account_id,
        region=region,
        app_name=app_name,
        requested_vpc_id=requested_vpc_id,
        requested_subnet_ids=requested_subnet_ids,
        explicit_database_url=explicit_database_url,
        bucket_name=bucket_name,
        ecr_repository=ecr_repository,
        image_tag=image_tag,
        image_uri=image_uri,
        cors_origins=cors_origins or "*",
        allowed_hosts=allowed_hosts or "*",
        rds_instance_identifier=rds_instance_identifier,
        rds_db_name=rds_db_name,
        rds_master_username=rds_master_username,
        rds_password_parameter_name=rds_password_parameter_name,
        published_release_tag=published_release_tag,
        published_image_ref=published_image_ref,
    )


def _build_runtime_env_vars(
    env_values: OrderedDict[str, str],
    config: _ResolvedAWSDeployConfig,
    *,
    database_url: str,
) -> OrderedDict[str, str]:
    final_env: OrderedDict[str, str] = OrderedDict()
    excluded = {
        "BACKEND_DATA_DIR",
        "BACKEND_SQLITE_PATH",
        "BACKEND_STORAGE_BACKEND",
        "BACKEND_OBJECT_STORE_PROVIDER",
        "BACKEND_OBJECT_STORE_NAME",
        "BACKEND_OBJECT_STORE_PREFIX",
        "BACKEND_DATABASE_URL",
        "PORT",
    }
    for key, value in env_values.items():
        if key in excluded:
            continue
        final_env[key] = value

    final_env["BACKEND_PROFILE"] = "production"
    final_env["BACKEND_STORAGE_BACKEND"] = "managed"
    final_env["BACKEND_OBJECT_STORE_PROVIDER"] = "s3"
    final_env["BACKEND_OBJECT_STORE_NAME"] = config.bucket_name
    final_env["BACKEND_OBJECT_STORE_PREFIX"] = config.app_name
    final_env["BACKEND_DATABASE_URL"] = database_url
    final_env["CORS_ORIGINS"] = config.cors_origins
    final_env["BACKEND_ALLOWED_HOSTS"] = config.allowed_hosts
    final_env["PORT"] = "8080"
    return final_env


def _run_aws_deploy_mutations(
    config: _ResolvedAWSDeployConfig,
    *,
    env_values: OrderedDict[str, str],
    stage_records: list[dict[str, object]],
    project_root: Path,
) -> _AWSDeployMutationResult:
    _ensure_s3_bucket(config, stage_records=stage_records)
    _ensure_ecr_repository(config, stage_records=stage_records)
    _docker_login_to_ecr(config, stage_records=stage_records)
    if config.image_source_mode == IMAGE_SOURCE_MODE_SOURCE_BUILD:
        _build_and_push_image(config, stage_records=stage_records, project_root=project_root)
    else:
        stage_records.append(
            _stage_ok(
                "publish_image",
                f"Skipped docker build/push for image_source_mode={config.image_source_mode}.",
            )
        )

    database_resolution = _resolve_or_provision_database(config, stage_records=stage_records)
    vpc_id, subnet_ids = _resolve_vpc_and_subnets(config)
    alb_security_group_id, ecs_security_group_id = _ensure_service_security_groups(
        config=config,
        vpc_id=vpc_id,
        rds_security_group_id=database_resolution.rds_security_group_id,
        stage_records=stage_records,
    )
    alb_arn, alb_dns_name = _ensure_application_load_balancer(
        config=config,
        subnet_ids=subnet_ids,
        alb_security_group_id=alb_security_group_id,
        stage_records=stage_records,
    )
    target_group_arn = _ensure_target_group(
        config=config,
        vpc_id=vpc_id,
        stage_records=stage_records,
    )
    _ensure_alb_listener(
        config=config,
        alb_arn=alb_arn,
        target_group_arn=target_group_arn,
        stage_records=stage_records,
    )
    cloudfront_distribution_id, cloudfront_domain_name = _ensure_cloudfront_distribution(
        config=config,
        alb_dns_name=alb_dns_name,
        stage_records=stage_records,
    )
    runtime_env = _build_runtime_env_vars(
        env_values,
        config,
        database_url=database_resolution.database_url,
    )
    runtime_env["BACKEND_ALLOWED_HOSTS"] = _compose_allowed_hosts(
        configured_hosts=config.allowed_hosts,
        cloudfront_domain_name=cloudfront_domain_name,
    )
    execution_role_arn = _ensure_ecs_execution_role(stage_records=stage_records)
    task_role_arn = _ensure_ecs_task_role(config=config, stage_records=stage_records)
    log_group_name = _ensure_ecs_log_group(config=config, stage_records=stage_records)
    cluster_name = _ensure_ecs_cluster(config=config, stage_records=stage_records)
    _ensure_ecs_service_linked_role(stage_records=stage_records)
    task_definition_arn = _register_task_definition(
        config=config,
        runtime_env=runtime_env,
        execution_role_arn=execution_role_arn,
        task_role_arn=task_role_arn,
        log_group_name=log_group_name,
        stage_records=stage_records,
    )
    service_name = _upsert_ecs_service(
        config=config,
        cluster_name=cluster_name,
        task_definition_arn=task_definition_arn,
        subnet_ids=subnet_ids,
        ecs_security_group_id=ecs_security_group_id,
        target_group_arn=target_group_arn,
        stage_records=stage_records,
    )
    _wait_for_ecs_service_stable(
        config=config,
        cluster_name=cluster_name,
        service_name=service_name,
        expected_task_definition_arn=task_definition_arn,
        stage_records=stage_records,
    )
    _wait_for_cloudfront_deployed(
        config=config,
        distribution_id=cloudfront_distribution_id,
        stage_records=stage_records,
    )
    service_url = _normalize_service_url(cloudfront_domain_name)
    return _AWSDeployMutationResult(
        database_url=database_resolution.database_url,
        ecs_cluster_name=cluster_name,
        ecs_service_name=service_name,
        task_definition_arn=task_definition_arn,
        alb_dns_name=alb_dns_name,
        cloudfront_distribution_id=cloudfront_distribution_id,
        cloudfront_domain_name=cloudfront_domain_name,
        service_url=service_url,
        resolved_vpc_id=vpc_id,
        resolved_subnet_ids=subnet_ids,
        alb_security_group_id=alb_security_group_id,
        ecs_security_group_id=ecs_security_group_id,
        rds_security_group_id=database_resolution.rds_security_group_id,
        used_external_database=database_resolution.used_external_database,
    )


def _ensure_s3_bucket(config: _ResolvedAWSDeployConfig, *, stage_records: list[dict[str, object]]) -> None:
    head = run_aws_text(["s3api", "head-bucket", "--bucket", config.bucket_name, "--region", config.region])
    if head.ok:
        stage_records.append(_stage_ok("s3_bucket", f"S3 bucket `{config.bucket_name}` is ready."))
        return

    lowered = (head.message or "").lower()
    if "forbidden" in lowered:
        raise DeployStageError(
            stage="s3_bucket",
            message=(
                f"S3 bucket `{config.bucket_name}` exists but is not accessible with current AWS credentials."
            ),
            action="Choose another --bucket value or fix S3 permissions.",
        )

    if "notfound" in lowered or "404" in lowered or "not found" in lowered:
        args = ["s3api", "create-bucket", "--bucket", config.bucket_name, "--region", config.region]
        if config.region != "us-east-1":
            args.extend(
                [
                    "--create-bucket-configuration",
                    _to_json_argument({"LocationConstraint": config.region}),
                ]
            )
        created = run_aws_json(args)
        if not created.ok:
            raise DeployStageError(
                stage="s3_bucket",
                message=created.message or "Unable to create S3 bucket.",
                action="Verify permissions for s3:CreateBucket and retry.",
            )
        stage_records.append(_stage_ok("s3_bucket", f"Created S3 bucket `{config.bucket_name}`."))
        return

    raise DeployStageError(
        stage="s3_bucket",
        message=head.message or "Unable to inspect S3 bucket.",
        action="Verify S3 permissions and bucket naming.",
    )


def _ensure_ecr_repository(config: _ResolvedAWSDeployConfig, *, stage_records: list[dict[str, object]]) -> None:
    describe = run_aws_json(
        [
            "ecr",
            "describe-repositories",
            "--region",
            config.region,
            "--repository-names",
            config.ecr_repository,
        ]
    )
    if describe.ok:
        stage_records.append(_stage_ok("ecr_repository", f"ECR repository `{config.ecr_repository}` is ready."))
        return

    message = (describe.message or "").lower()
    if "repositorynotfoundexception" not in message and "not found" not in message:
        raise DeployStageError(
            stage="ecr_repository",
            message=describe.message or "Unable to inspect ECR repository.",
            action="Verify ecr:DescribeRepositories permissions and retry.",
        )

    created = run_aws_json(
        [
            "ecr",
            "create-repository",
            "--region",
            config.region,
            "--repository-name",
            config.ecr_repository,
        ]
    )
    if not created.ok:
        raise DeployStageError(
            stage="ecr_repository",
            message=created.message or "Unable to create ECR repository.",
            action="Ensure ecr:CreateRepository permission or pre-create repository.",
        )
    stage_records.append(_stage_ok("ecr_repository", f"Created ECR repository `{config.ecr_repository}`."))


def _ensure_apprunner_ecr_access_role(*, stage_records: list[dict[str, object]]) -> str:
    role = run_aws_json(["iam", "get-role", "--role-name", APP_RUNNER_ECR_ROLE_NAME])
    if role.ok and isinstance(role.value, dict):
        role_payload = role.value.get("Role")
        if isinstance(role_payload, dict):
            role_arn = _read_dict_string(role_payload, "Arn")
            if role_arn:
                stage_records.append(_stage_ok("iam_apprunner_ecr_role", f"IAM role `{APP_RUNNER_ECR_ROLE_NAME}` is ready."))
                return role_arn

    lowered = (role.message or "").lower()
    if "nosuchentity" not in lowered and "not found" not in lowered:
        raise DeployStageError(
            stage="iam_apprunner_ecr_role",
            message=role.message or "Unable to inspect IAM role for App Runner ECR access.",
            action="Verify IAM permissions for iam:GetRole.",
        )

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "build.apprunner.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    created = run_aws_json(
        [
            "iam",
            "create-role",
            "--role-name",
            APP_RUNNER_ECR_ROLE_NAME,
            "--assume-role-policy-document",
            _to_json_argument(trust_policy),
        ]
    )
    if not created.ok or not isinstance(created.value, dict):
        raise DeployStageError(
            stage="iam_apprunner_ecr_role",
            message=created.message or "Unable to create IAM role for App Runner ECR access.",
            action="Grant iam:CreateRole and retry.",
        )
    attach = run_aws_json(
        [
            "iam",
            "attach-role-policy",
            "--role-name",
            APP_RUNNER_ECR_ROLE_NAME,
            "--policy-arn",
            "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess",
        ]
    )
    if not attach.ok:
        raise DeployStageError(
            stage="iam_apprunner_ecr_role",
            message=attach.message or "Unable to attach App Runner ECR access policy.",
            action="Grant iam:AttachRolePolicy and retry.",
        )
    stage_records.append(_stage_ok("iam_apprunner_ecr_role", f"Created IAM role `{APP_RUNNER_ECR_ROLE_NAME}`."))
    created_role = created.value.get("Role")
    if not isinstance(created_role, dict):
        raise DeployStageError(
            stage="iam_apprunner_ecr_role",
            message="IAM create-role response did not include role details.",
            action="Retry deploy.",
        )
    role_arn = _read_dict_string(created_role, "Arn")
    if not role_arn:
        raise DeployStageError(
            stage="iam_apprunner_ecr_role",
            message="Unable to resolve IAM role ARN for App Runner ECR role.",
            action="Retry deploy.",
        )
    return role_arn


def _ensure_apprunner_instance_role(
    *,
    config: _ResolvedAWSDeployConfig,
    stage_records: list[dict[str, object]],
) -> str:
    role_name = f"{config.app_name}-{APP_RUNNER_INSTANCE_ROLE_SUFFIX}"
    role = run_aws_json(["iam", "get-role", "--role-name", role_name])
    if role.ok and isinstance(role.value, dict):
        role_payload = role.value.get("Role")
        if isinstance(role_payload, dict):
            role_arn = _read_dict_string(role_payload, "Arn")
            if role_arn:
                _put_apprunner_instance_role_policy(
                    role_name=role_name,
                    bucket_name=config.bucket_name,
                )
                stage_records.append(
                    _stage_ok("iam_apprunner_instance_role", f"IAM role `{role_name}` is ready.")
                )
                return role_arn

    lowered = (role.message or "").lower()
    if "nosuchentity" not in lowered and "not found" not in lowered:
        raise DeployStageError(
            stage="iam_apprunner_instance_role",
            message=role.message or "Unable to inspect IAM role for App Runner runtime access.",
            action="Verify IAM permissions for iam:GetRole.",
        )

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "tasks.apprunner.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    created = run_aws_json(
        [
            "iam",
            "create-role",
            "--role-name",
            role_name,
            "--assume-role-policy-document",
            _to_json_argument(trust_policy),
        ]
    )
    if not created.ok or not isinstance(created.value, dict):
        raise DeployStageError(
            stage="iam_apprunner_instance_role",
            message=created.message or "Unable to create IAM role for App Runner runtime access.",
            action="Grant iam:CreateRole and retry.",
        )
    _put_apprunner_instance_role_policy(role_name=role_name, bucket_name=config.bucket_name)
    stage_records.append(_stage_ok("iam_apprunner_instance_role", f"Created IAM role `{role_name}`."))
    created_role = created.value.get("Role")
    if not isinstance(created_role, dict):
        raise DeployStageError(
            stage="iam_apprunner_instance_role",
            message="IAM create-role response did not include role details.",
            action="Retry deploy.",
        )
    role_arn = _read_dict_string(created_role, "Arn")
    if not role_arn:
        raise DeployStageError(
            stage="iam_apprunner_instance_role",
            message="Unable to resolve IAM role ARN for App Runner runtime role.",
            action="Retry deploy.",
        )
    return role_arn


def _put_apprunner_instance_role_policy(*, role_name: str, bucket_name: str) -> None:
    policy_document = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:ListBucket"],
                "Resource": [f"arn:aws:s3:::{bucket_name}"],
            },
            {
                "Effect": "Allow",
                "Action": [
                    "s3:GetObject",
                    "s3:PutObject",
                    "s3:DeleteObject",
                ],
                "Resource": [f"arn:aws:s3:::{bucket_name}/*"],
            },
        ],
    }
    put_policy = run_aws_json(
        [
            "iam",
            "put-role-policy",
            "--role-name",
            role_name,
            "--policy-name",
            APP_RUNNER_INSTANCE_POLICY_NAME,
            "--policy-document",
            _to_json_argument(policy_document),
        ]
    )
    if not put_policy.ok:
        raise DeployStageError(
            stage="iam_apprunner_instance_role",
            message=put_policy.message or "Unable to apply inline App Runner runtime policy.",
            action="Grant iam:PutRolePolicy and retry.",
        )


def _docker_login_to_ecr(config: _ResolvedAWSDeployConfig, *, stage_records: list[dict[str, object]]) -> None:
    login = run_aws_text(["ecr", "get-login-password", "--region", config.region])
    if not login.ok or not isinstance(login.value, str) or not login.value:
        raise DeployStageError(
            stage="docker_login",
            message=login.message or "Unable to fetch ECR docker login password.",
            action="Verify AWS auth and ECR permissions.",
        )

    registry = f"{config.account_id}.dkr.ecr.{config.region}.amazonaws.com"
    completed = subprocess.run(
        ["docker", "login", "--username", "AWS", "--password-stdin", registry],
        input=login.value,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise DeployStageError(
            stage="docker_login",
            message=(completed.stderr or completed.stdout).strip() or "docker login to ECR failed.",
            action="Ensure Docker is running and ECR auth is configured.",
        )
    stage_records.append(_stage_ok("docker_login", f"Logged into ECR registry `{registry}`."))


def _build_and_push_image(
    config: _ResolvedAWSDeployConfig,
    *,
    stage_records: list[dict[str, object]],
    project_root: Path,
) -> None:
    completed = subprocess.run(
        [
            "docker",
            "buildx",
            "build",
            "--platform",
            "linux/amd64",
            "-f",
            "backend/Dockerfile",
            "-t",
            config.image_uri,
            "--push",
            ".",
        ],
        cwd=str(project_root),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise DeployStageError(
            stage="publish_image",
            message=(completed.stderr or completed.stdout).strip() or "docker buildx build --push failed.",
            action="Verify Docker buildx is available and registry push permissions are granted.",
        )
    stage_records.append(_stage_ok("publish_image", f"Built and pushed `{config.image_uri}`."))


@dataclass(frozen=True, slots=True)
class _DatabaseResolution:
    database_url: str
    resolved_vpc_id: str | None
    resolved_subnet_ids: tuple[str, ...]
    rds_security_group_id: str | None
    used_external_database: bool


def _resolve_or_provision_database(
    config: _ResolvedAWSDeployConfig,
    *,
    stage_records: list[dict[str, object]],
) -> _DatabaseResolution:
    if config.explicit_database_url:
        stage_records.append(_stage_ok("rds_database", "Using externally provided BACKEND_DATABASE_URL."))
        return _DatabaseResolution(
            database_url=config.explicit_database_url,
            resolved_vpc_id=None,
            resolved_subnet_ids=(),
            rds_security_group_id=None,
            used_external_database=True,
        )

    vpc_id, subnet_ids = _resolve_vpc_and_subnets(config)
    subnet_group_name = f"{config.rds_instance_identifier}-subnets"
    _ensure_db_subnet_group(
        region=config.region,
        subnet_group_name=subnet_group_name,
        subnet_ids=subnet_ids,
        stage_records=stage_records,
    )
    security_group_id = _ensure_rds_security_group(
        region=config.region,
        vpc_id=vpc_id,
        app_name=config.app_name,
        stage_records=stage_records,
    )
    database_url = _ensure_rds_instance(
        config,
        subnet_group_name=subnet_group_name,
        security_group_id=security_group_id,
        stage_records=stage_records,
    )
    return _DatabaseResolution(
        database_url=database_url,
        resolved_vpc_id=vpc_id,
        resolved_subnet_ids=subnet_ids,
        rds_security_group_id=security_group_id,
        used_external_database=False,
    )


def _resolve_vpc_and_subnets(config: _ResolvedAWSDeployConfig) -> tuple[str, tuple[str, ...]]:
    if config.requested_vpc_id:
        vpc_id = config.requested_vpc_id
    else:
        default_vpc_result = run_aws_json(
            [
                "ec2",
                "describe-vpcs",
                "--region",
                config.region,
                "--filters",
                "Name=isDefault,Values=true",
            ]
        )
        if not default_vpc_result.ok or not isinstance(default_vpc_result.value, dict):
            raise DeployStageError(
                stage="rds_network",
                message=default_vpc_result.message or "Unable to resolve default VPC.",
                action="Pass --vpc-id or ensure ec2:DescribeVpcs permissions.",
            )
        vpcs = default_vpc_result.value.get("Vpcs")
        if not isinstance(vpcs, list) or len(vpcs) == 0 or not isinstance(vpcs[0], dict):
            raise DeployStageError(
                stage="rds_network",
                message="No default VPC was found in the selected AWS region.",
                action="Pass --vpc-id and --subnet-ids for an existing VPC.",
            )
        vpc_id = _read_dict_string(vpcs[0], "VpcId")
        if not vpc_id:
            raise DeployStageError(
                stage="rds_network",
                message="Unable to resolve VPC id from default VPC response.",
                action="Pass --vpc-id explicitly.",
            )

    if config.requested_subnet_ids:
        subnet_ids = config.requested_subnet_ids
    else:
        subnet_result = run_aws_json(
            [
                "ec2",
                "describe-subnets",
                "--region",
                config.region,
                "--filters",
                f"Name=vpc-id,Values={vpc_id}",
                "Name=default-for-az,Values=true",
            ]
        )
        if not subnet_result.ok or not isinstance(subnet_result.value, dict):
            raise DeployStageError(
                stage="rds_network",
                message=subnet_result.message or "Unable to resolve default subnets for VPC.",
                action="Pass --subnet-ids and ensure ec2:DescribeSubnets permissions.",
            )
        subnet_ids = _select_subnets_for_rds(subnet_result.value)

    if len(subnet_ids) < 2:
        raise DeployStageError(
            stage="rds_network",
            message="RDS requires at least two subnets in distinct availability zones.",
            action="Provide --subnet-ids with at least two subnets across different AZs.",
        )
    return vpc_id, subnet_ids


def _select_subnets_for_rds(payload: dict[str, object]) -> tuple[str, ...]:
    subnets = payload.get("Subnets")
    if not isinstance(subnets, list):
        return ()
    selected: list[str] = []
    seen_az: set[str] = set()
    sortable: list[tuple[str, str]] = []
    for subnet in subnets:
        if not isinstance(subnet, dict):
            continue
        subnet_id = _read_dict_string(subnet, "SubnetId")
        az = _read_dict_string(subnet, "AvailabilityZone")
        if subnet_id and az:
            sortable.append((az, subnet_id))
    sortable.sort()
    for az, subnet_id in sortable:
        if az in seen_az:
            continue
        seen_az.add(az)
        selected.append(subnet_id)
        if len(selected) >= 3:
            break
    return tuple(selected)


def _ensure_db_subnet_group(
    *,
    region: str,
    subnet_group_name: str,
    subnet_ids: tuple[str, ...],
    stage_records: list[dict[str, object]],
) -> None:
    described = run_aws_json(
        [
            "rds",
            "describe-db-subnet-groups",
            "--region",
            region,
            "--db-subnet-group-name",
            subnet_group_name,
        ]
    )
    if described.ok:
        stage_records.append(_stage_ok("rds_subnet_group", f"RDS subnet group `{subnet_group_name}` is ready."))
        return
    lowered = (described.message or "").lower()
    if "dbsubnetgroupnotfoundfault" not in lowered and "not found" not in lowered:
        raise DeployStageError(
            stage="rds_subnet_group",
            message=described.message or "Unable to inspect RDS DB subnet group.",
            action="Verify rds:DescribeDBSubnetGroups permissions.",
        )
    created = run_aws_json(
        [
            "rds",
            "create-db-subnet-group",
            "--region",
            region,
            "--db-subnet-group-name",
            subnet_group_name,
            "--db-subnet-group-description",
            "PortWorld managed DB subnet group",
            "--subnet-ids",
            *subnet_ids,
        ]
    )
    if not created.ok:
        raise DeployStageError(
            stage="rds_subnet_group",
            message=created.message or "Unable to create RDS DB subnet group.",
            action="Verify rds:CreateDBSubnetGroup permissions and subnet ids.",
        )
    stage_records.append(_stage_ok("rds_subnet_group", f"Created RDS subnet group `{subnet_group_name}`."))


def _ensure_rds_security_group(
    *,
    region: str,
    vpc_id: str,
    app_name: str,
    stage_records: list[dict[str, object]],
) -> str:
    group_name = f"{app_name}-pg-sg"
    described = run_aws_json(
        [
            "ec2",
            "describe-security-groups",
            "--region",
            region,
            "--filters",
            f"Name=group-name,Values={group_name}",
            f"Name=vpc-id,Values={vpc_id}",
        ]
    )
    if not described.ok or not isinstance(described.value, dict):
        raise DeployStageError(
            stage="rds_security_group",
            message=described.message or "Unable to inspect RDS security groups.",
            action="Verify ec2:DescribeSecurityGroups permissions.",
        )
    groups = described.value.get("SecurityGroups")
    if isinstance(groups, list) and len(groups) > 0 and isinstance(groups[0], dict):
        existing_group_id = _read_dict_string(groups[0], "GroupId")
        if existing_group_id:
            _ensure_rds_security_group_ingress(region=region, group_id=existing_group_id)
            stage_records.append(_stage_ok("rds_security_group", f"RDS security group `{existing_group_id}` is ready."))
            return existing_group_id

    created = run_aws_json(
        [
            "ec2",
            "create-security-group",
            "--region",
            region,
            "--group-name",
            group_name,
            "--description",
            "PortWorld managed PostgreSQL ingress",
            "--vpc-id",
            vpc_id,
        ]
    )
    if not created.ok or not isinstance(created.value, dict):
        raise DeployStageError(
            stage="rds_security_group",
            message=created.message or "Unable to create RDS security group.",
            action="Verify ec2:CreateSecurityGroup permissions and VPC configuration.",
        )
    group_id = _read_dict_string(created.value, "GroupId")
    if not group_id:
        raise DeployStageError(
            stage="rds_security_group",
            message="Unable to read created RDS security group id.",
            action="Retry deploy.",
        )
    _ensure_rds_security_group_ingress(region=region, group_id=group_id)
    stage_records.append(_stage_ok("rds_security_group", f"Created RDS security group `{group_id}`."))
    return group_id


def _ensure_rds_security_group_ingress(*, region: str, group_id: str) -> None:
    ingress = run_aws_json(
        [
            "ec2",
            "authorize-security-group-ingress",
            "--region",
            region,
            "--group-id",
            group_id,
            "--ip-permissions",
            _to_json_argument(
                [
                    {
                        "IpProtocol": "tcp",
                        "FromPort": 5432,
                        "ToPort": 5432,
                        "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "PortWorld MVP ingress"}],
                    }
                ]
            ),
        ]
    )
    if ingress.ok:
        return
    lowered = (ingress.message or "").lower()
    if "invalidpermission.duplicate" in lowered or "already exists" in lowered:
        return
    raise DeployStageError(
        stage="rds_security_group",
        message=ingress.message or "Unable to configure PostgreSQL ingress rule for RDS security group.",
        action="Verify ec2:AuthorizeSecurityGroupIngress permissions.",
    )


def _ensure_rds_instance(
    config: _ResolvedAWSDeployConfig,
    *,
    subnet_group_name: str,
    security_group_id: str,
    stage_records: list[dict[str, object]],
) -> str:
    described = run_aws_json(
        [
            "rds",
            "describe-db-instances",
            "--region",
            config.region,
            "--db-instance-identifier",
            config.rds_instance_identifier,
        ]
    )
    if described.ok and isinstance(described.value, dict):
        password = _resolve_rds_password(config)
        endpoint = _wait_for_rds_endpoint(
            region=config.region,
            db_instance_identifier=config.rds_instance_identifier,
            stage_records=stage_records,
        )
        stage_records.append(_stage_ok("rds_instance", f"RDS instance `{config.rds_instance_identifier}` is ready."))
        return _build_postgres_url(
            username=config.rds_master_username,
            password=password,
            host=endpoint[0],
            port=endpoint[1],
            db_name=config.rds_db_name,
        )

    lowered = (described.message or "").lower()
    if "dbinstancenotfound" not in lowered and "not found" not in lowered:
        raise DeployStageError(
            stage="rds_instance",
            message=described.message or "Unable to inspect RDS instance.",
            action="Verify rds:DescribeDBInstances permissions.",
        )

    password = _generate_rds_password()
    created = run_aws_json(
        [
            "rds",
            "create-db-instance",
            "--region",
            config.region,
            "--db-instance-identifier",
            config.rds_instance_identifier,
            "--db-instance-class",
            RDS_INSTANCE_CLASS,
            "--engine",
            "postgres",
            "--allocated-storage",
            RDS_STORAGE_GB,
            "--storage-type",
            "gp3",
            "--master-username",
            config.rds_master_username,
            "--master-user-password",
            password,
            "--db-name",
            config.rds_db_name,
            "--publicly-accessible",
            "--no-multi-az",
            "--backup-retention-period",
            "1",
            "--db-subnet-group-name",
            subnet_group_name,
            "--vpc-security-group-ids",
            security_group_id,
            "--no-deletion-protection",
        ]
    )
    if not created.ok:
        raise DeployStageError(
            stage="rds_instance",
            message=created.message or "Unable to create RDS PostgreSQL instance.",
            action="Verify RDS quotas/permissions and retry.",
        )
    _store_rds_password(config, password)
    endpoint = _wait_for_rds_endpoint(
        region=config.region,
        db_instance_identifier=config.rds_instance_identifier,
        stage_records=stage_records,
    )
    stage_records.append(_stage_ok("rds_instance", f"Created RDS instance `{config.rds_instance_identifier}`."))
    return _build_postgres_url(
        username=config.rds_master_username,
        password=password,
        host=endpoint[0],
        port=endpoint[1],
        db_name=config.rds_db_name,
    )


def _wait_for_rds_endpoint(
    *,
    region: str,
    db_instance_identifier: str,
    stage_records: list[dict[str, object]],
) -> tuple[str, int]:
    wait = run_aws_text(
        [
            "rds",
            "wait",
            "db-instance-available",
            "--region",
            region,
            "--db-instance-identifier",
            db_instance_identifier,
        ]
    )
    if not wait.ok:
        raise DeployStageError(
            stage="rds_instance_wait_available",
            message=wait.message or "RDS instance did not become available.",
            action="Inspect AWS RDS events/logs and retry.",
        )
    stage_records.append(_stage_ok("rds_instance_wait_available", f"RDS instance `{db_instance_identifier}` is available."))

    described = run_aws_json(
        [
            "rds",
            "describe-db-instances",
            "--region",
            region,
            "--db-instance-identifier",
            db_instance_identifier,
        ]
    )
    if not described.ok or not isinstance(described.value, dict):
        raise DeployStageError(
            stage="rds_instance_wait_available",
            message=described.message or "Unable to resolve RDS endpoint.",
            action="Verify rds:DescribeDBInstances permissions.",
        )
    endpoint = _extract_rds_endpoint(described.value)
    if endpoint is None:
        raise DeployStageError(
            stage="rds_instance_wait_available",
            message="RDS endpoint address/port is not available.",
            action="Retry deploy after RDS instance fully initializes.",
        )
    return endpoint


def _extract_rds_endpoint(payload: dict[str, object]) -> tuple[str, int] | None:
    instances = payload.get("DBInstances")
    if not isinstance(instances, list) or len(instances) == 0 or not isinstance(instances[0], dict):
        return None
    endpoint = instances[0].get("Endpoint")
    if not isinstance(endpoint, dict):
        return None
    address = _read_dict_string(endpoint, "Address")
    port = endpoint.get("Port")
    if address is None or not isinstance(port, int):
        return None
    return (address, port)


def _store_rds_password(config: _ResolvedAWSDeployConfig, password: str) -> None:
    store = run_aws_json(
        [
            "ssm",
            "put-parameter",
            "--region",
            config.region,
            "--name",
            config.rds_password_parameter_name,
            "--type",
            "SecureString",
            "--value",
            password,
            "--overwrite",
        ]
    )
    if not store.ok:
        raise DeployStageError(
            stage="rds_password_store",
            message=store.message or "Unable to persist RDS password in SSM Parameter Store.",
            action="Grant ssm:PutParameter permissions or pass --database-url explicitly.",
        )


def _resolve_rds_password(config: _ResolvedAWSDeployConfig) -> str:
    read = run_aws_json(
        [
            "ssm",
            "get-parameter",
            "--region",
            config.region,
            "--name",
            config.rds_password_parameter_name,
            "--with-decryption",
        ]
    )
    if not read.ok or not isinstance(read.value, dict):
        raise DeployStageError(
            stage="rds_password_read",
            message=(
                "RDS instance already exists but no password could be read from SSM Parameter Store."
                if not read.message
                else read.message
            ),
            action=(
                "Pass --database-url explicitly, or grant ssm:GetParameter and ensure "
                f"`{config.rds_password_parameter_name}` exists."
            ),
        )
    parameter = read.value.get("Parameter")
    if not isinstance(parameter, dict):
        raise DeployStageError(
            stage="rds_password_read",
            message="SSM get-parameter response missing Parameter payload.",
            action="Pass --database-url explicitly or recreate database password parameter.",
        )
    value = _read_dict_string(parameter, "Value")
    if not value:
        raise DeployStageError(
            stage="rds_password_read",
            message="RDS password parameter was empty.",
            action="Pass --database-url explicitly or rewrite password parameter.",
        )
    return value


def _build_postgres_url(*, username: str, password: str, host: str, port: int, db_name: str) -> str:
    return f"postgresql://{quote(username)}:{quote(password)}@{host}:{port}/{db_name}"


def _compose_allowed_hosts(*, configured_hosts: str, cloudfront_domain_name: str) -> str:
    existing = [part.strip() for part in configured_hosts.split(",") if part.strip()]
    if cloudfront_domain_name not in existing:
        existing.append(cloudfront_domain_name)
    return ",".join(existing)


def _ensure_service_security_groups(
    *,
    config: _ResolvedAWSDeployConfig,
    vpc_id: str,
    rds_security_group_id: str | None,
    stage_records: list[dict[str, object]],
) -> tuple[str, str]:
    alb_group_name = f"{config.app_name}-alb-sg"
    ecs_group_name = f"{config.app_name}-ecs-sg"
    alb_sg = _ensure_security_group(
        region=config.region,
        vpc_id=vpc_id,
        group_name=alb_group_name,
        description="PortWorld ALB ingress",
    )
    ecs_sg = _ensure_security_group(
        region=config.region,
        vpc_id=vpc_id,
        group_name=ecs_group_name,
        description="PortWorld ECS ingress",
    )
    _authorize_ingress_cidr(region=config.region, group_id=alb_sg, port=80, cidr="0.0.0.0/0")
    _authorize_ingress_sg(region=config.region, group_id=ecs_sg, port=8080, source_group_id=alb_sg)
    if rds_security_group_id:
        _authorize_ingress_sg(region=config.region, group_id=rds_security_group_id, port=5432, source_group_id=ecs_sg)
    stage_records.append(_stage_ok("service_security_groups", f"Configured ALB `{alb_sg}` and ECS `{ecs_sg}` security groups."))
    return alb_sg, ecs_sg


def _ensure_security_group(*, region: str, vpc_id: str, group_name: str, description: str) -> str:
    described = run_aws_json(
        [
            "ec2",
            "describe-security-groups",
            "--region",
            region,
            "--filters",
            f"Name=group-name,Values={group_name}",
            f"Name=vpc-id,Values={vpc_id}",
        ]
    )
    if not described.ok or not isinstance(described.value, dict):
        raise DeployStageError(
            stage="service_security_groups",
            message=described.message or "Unable to inspect service security groups.",
            action="Verify ec2:DescribeSecurityGroups permissions.",
        )
    groups = described.value.get("SecurityGroups")
    if isinstance(groups, list) and groups and isinstance(groups[0], dict):
        group_id = _read_dict_string(groups[0], "GroupId")
        if group_id:
            return group_id
    created = run_aws_json(
        [
            "ec2",
            "create-security-group",
            "--region",
            region,
            "--group-name",
            group_name,
            "--description",
            description,
            "--vpc-id",
            vpc_id,
        ]
    )
    if not created.ok or not isinstance(created.value, dict):
        raise DeployStageError(
            stage="service_security_groups",
            message=created.message or f"Unable to create security group `{group_name}`.",
            action="Verify ec2:CreateSecurityGroup permissions.",
        )
    group_id = _read_dict_string(created.value, "GroupId")
    if not group_id:
        raise DeployStageError(
            stage="service_security_groups",
            message=f"Unable to resolve security group id for `{group_name}`.",
            action="Retry deploy.",
        )
    return group_id


def _authorize_ingress_cidr(*, region: str, group_id: str, port: int, cidr: str) -> None:
    result = run_aws_json(
        [
            "ec2",
            "authorize-security-group-ingress",
            "--region",
            region,
            "--group-id",
            group_id,
            "--ip-permissions",
            _to_json_argument(
                [
                    {
                        "IpProtocol": "tcp",
                        "FromPort": port,
                        "ToPort": port,
                        "IpRanges": [{"CidrIp": cidr}],
                    }
                ]
            ),
        ]
    )
    if result.ok:
        return
    lowered = (result.message or "").lower()
    if "invalidpermission.duplicate" in lowered or "already exists" in lowered:
        return
    raise DeployStageError(
        stage="service_security_groups",
        message=result.message or f"Unable to configure CIDR ingress on security group `{group_id}`.",
        action="Verify ec2:AuthorizeSecurityGroupIngress permissions.",
    )


def _authorize_ingress_sg(*, region: str, group_id: str, port: int, source_group_id: str) -> None:
    result = run_aws_json(
        [
            "ec2",
            "authorize-security-group-ingress",
            "--region",
            region,
            "--group-id",
            group_id,
            "--ip-permissions",
            _to_json_argument(
                [
                    {
                        "IpProtocol": "tcp",
                        "FromPort": port,
                        "ToPort": port,
                        "UserIdGroupPairs": [{"GroupId": source_group_id}],
                    }
                ]
            ),
        ]
    )
    if result.ok:
        return
    lowered = (result.message or "").lower()
    if "invalidpermission.duplicate" in lowered or "already exists" in lowered:
        return
    raise DeployStageError(
        stage="service_security_groups",
        message=result.message or f"Unable to configure SG ingress on security group `{group_id}`.",
        action="Verify ec2:AuthorizeSecurityGroupIngress permissions.",
    )


def _ensure_application_load_balancer(
    *,
    config: _ResolvedAWSDeployConfig,
    subnet_ids: tuple[str, ...],
    alb_security_group_id: str,
    stage_records: list[dict[str, object]],
) -> tuple[str, str]:
    alb_name = f"{config.app_name}-alb"[:32]
    described = run_aws_json(
        [
            "elbv2",
            "describe-load-balancers",
            "--region",
            config.region,
            "--names",
            alb_name,
        ]
    )
    if described.ok and isinstance(described.value, dict):
        lbs = described.value.get("LoadBalancers")
        if isinstance(lbs, list) and lbs and isinstance(lbs[0], dict):
            alb_arn = _read_dict_string(lbs[0], "LoadBalancerArn")
            dns_name = _read_dict_string(lbs[0], "DNSName")
            if alb_arn and dns_name:
                stage_records.append(_stage_ok("alb", f"ALB `{alb_name}` is ready."))
                return alb_arn, dns_name
    created = run_aws_json(
        [
            "elbv2",
            "create-load-balancer",
            "--region",
            config.region,
            "--name",
            alb_name,
            "--type",
            "application",
            "--scheme",
            "internet-facing",
            "--security-groups",
            alb_security_group_id,
            "--subnets",
            *subnet_ids,
        ]
    )
    if not created.ok or not isinstance(created.value, dict):
        raise DeployStageError(
            stage="alb",
            message=created.message or "Unable to create ALB.",
            action="Verify elbv2:CreateLoadBalancer permissions.",
        )
    lbs = created.value.get("LoadBalancers")
    if not isinstance(lbs, list) or not lbs or not isinstance(lbs[0], dict):
        raise DeployStageError(
            stage="alb",
            message="ALB create response missing payload.",
            action="Retry deploy.",
        )
    alb_arn = _read_dict_string(lbs[0], "LoadBalancerArn")
    dns_name = _read_dict_string(lbs[0], "DNSName")
    if not alb_arn or not dns_name:
        raise DeployStageError(
            stage="alb",
            message="ALB create response missing ARN or DNS name.",
            action="Retry deploy.",
        )
    stage_records.append(_stage_ok("alb", f"Created ALB `{alb_name}`."))
    return alb_arn, dns_name


def _ensure_target_group(
    *,
    config: _ResolvedAWSDeployConfig,
    vpc_id: str,
    stage_records: list[dict[str, object]],
) -> str:
    tg_name = f"{config.app_name}-tg"[:32]
    described = run_aws_json(
        [
            "elbv2",
            "describe-target-groups",
            "--region",
            config.region,
            "--names",
            tg_name,
        ]
    )
    if described.ok and isinstance(described.value, dict):
        groups = described.value.get("TargetGroups")
        if isinstance(groups, list) and groups and isinstance(groups[0], dict):
            tg_arn = _read_dict_string(groups[0], "TargetGroupArn")
            if tg_arn:
                stage_records.append(_stage_ok("target_group", f"Target group `{tg_name}` is ready."))
                return tg_arn
    created = run_aws_json(
        [
            "elbv2",
            "create-target-group",
            "--region",
            config.region,
            "--name",
            tg_name,
            "--protocol",
            "HTTP",
            "--port",
            "8080",
            "--target-type",
            "ip",
            "--vpc-id",
            vpc_id,
            "--health-check-protocol",
            "HTTP",
            "--health-check-path",
            "/livez",
            "--matcher",
            "HttpCode=200-499",
        ]
    )
    if not created.ok or not isinstance(created.value, dict):
        raise DeployStageError(
            stage="target_group",
            message=created.message or "Unable to create target group.",
            action="Verify elbv2:CreateTargetGroup permissions.",
        )
    groups = created.value.get("TargetGroups")
    if not isinstance(groups, list) or not groups or not isinstance(groups[0], dict):
        raise DeployStageError(
            stage="target_group",
            message="Target group create response missing payload.",
            action="Retry deploy.",
        )
    tg_arn = _read_dict_string(groups[0], "TargetGroupArn")
    if not tg_arn:
        raise DeployStageError(
            stage="target_group",
            message="Target group create response missing ARN.",
            action="Retry deploy.",
        )
    stage_records.append(_stage_ok("target_group", f"Created target group `{tg_name}`."))
    return tg_arn


def _ensure_alb_listener(
    *,
    config: _ResolvedAWSDeployConfig,
    alb_arn: str,
    target_group_arn: str,
    stage_records: list[dict[str, object]],
) -> None:
    described = run_aws_json(
        [
            "elbv2",
            "describe-listeners",
            "--region",
            config.region,
            "--load-balancer-arn",
            alb_arn,
        ]
    )
    if described.ok and isinstance(described.value, dict):
        listeners = described.value.get("Listeners")
        if isinstance(listeners, list):
            for listener in listeners:
                if not isinstance(listener, dict):
                    continue
                port = listener.get("Port")
                arn = _read_dict_string(listener, "ListenerArn")
                if port == 80 and arn:
                    modified = run_aws_json(
                        [
                            "elbv2",
                            "modify-listener",
                            "--region",
                            config.region,
                            "--listener-arn",
                            arn,
                            "--default-actions",
                            _to_json_argument(
                                [
                                    {
                                        "Type": "forward",
                                        "TargetGroupArn": target_group_arn,
                                    }
                                ]
                            ),
                        ]
                    )
                    if not modified.ok:
                        raise DeployStageError(
                            stage="alb_listener",
                            message=modified.message or "Unable to update ALB listener.",
                            action="Verify elbv2:ModifyListener permissions.",
                        )
                    stage_records.append(_stage_ok("alb_listener", "ALB listener on port 80 is ready."))
                    return
    created = run_aws_json(
        [
            "elbv2",
            "create-listener",
            "--region",
            config.region,
            "--load-balancer-arn",
            alb_arn,
            "--protocol",
            "HTTP",
            "--port",
            "80",
            "--default-actions",
            _to_json_argument(
                [
                    {
                        "Type": "forward",
                        "TargetGroupArn": target_group_arn,
                    }
                ]
            ),
        ]
    )
    if not created.ok:
        raise DeployStageError(
            stage="alb_listener",
            message=created.message or "Unable to create ALB listener.",
            action="Verify elbv2:CreateListener permissions.",
        )
    stage_records.append(_stage_ok("alb_listener", "Created ALB listener on port 80."))


def _ensure_cloudfront_distribution(
    *,
    config: _ResolvedAWSDeployConfig,
    alb_dns_name: str,
    stage_records: list[dict[str, object]],
) -> tuple[str, str]:
    comment = f"PortWorld managed {config.app_name}"
    listed = run_aws_json(["cloudfront", "list-distributions"])
    if listed.ok and isinstance(listed.value, dict):
        dist_list = ((listed.value.get("DistributionList") or {}) if isinstance(listed.value.get("DistributionList"), dict) else {})
        items = dist_list.get("Items")
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                if _read_dict_string(item, "Comment") != comment:
                    continue
                dist_id = _read_dict_string(item, "Id")
                domain_name = _read_dict_string(item, "DomainName")
                if dist_id and domain_name:
                    stage_records.append(_stage_ok("cloudfront", f"CloudFront distribution `{dist_id}` is ready."))
                    return dist_id, domain_name

    caller_reference = f"{config.app_name}-{_now_ms()}"
    distribution_config = {
        "CallerReference": caller_reference,
        "Comment": comment,
        "Enabled": True,
        "Origins": {
            "Quantity": 1,
            "Items": [
                {
                    "Id": "alb-origin",
                    "DomainName": alb_dns_name,
                    "CustomOriginConfig": {
                        "HTTPPort": 80,
                        "HTTPSPort": 443,
                        "OriginProtocolPolicy": "http-only",
                        "OriginSslProtocols": {"Quantity": 1, "Items": ["TLSv1.2"]},
                    },
                }
            ],
        },
        "DefaultCacheBehavior": {
            "TargetOriginId": "alb-origin",
            "ViewerProtocolPolicy": "redirect-to-https",
            "AllowedMethods": {
                "Quantity": 7,
                "Items": ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"],
                "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]},
            },
            "Compress": True,
            "CachePolicyId": MANAGED_CACHE_POLICY_CACHING_DISABLED,
            "OriginRequestPolicyId": MANAGED_ORIGIN_REQUEST_POLICY_ALL_VIEWER,
        },
    }
    created = run_aws_json(
        [
            "cloudfront",
            "create-distribution",
            "--distribution-config",
            _to_json_argument(distribution_config),
        ]
    )
    if not created.ok or not isinstance(created.value, dict):
        raise DeployStageError(
            stage="cloudfront",
            message=created.message or "Unable to create CloudFront distribution.",
            action="Verify cloudfront:CreateDistribution permissions.",
        )
    dist = created.value.get("Distribution")
    if not isinstance(dist, dict):
        raise DeployStageError(
            stage="cloudfront",
            message="CloudFront create response missing Distribution payload.",
            action="Retry deploy.",
        )
    dist_id = _read_dict_string(dist, "Id")
    domain_name = _read_dict_string(dist, "DomainName")
    if not dist_id or not domain_name:
        raise DeployStageError(
            stage="cloudfront",
            message="CloudFront create response missing Id or DomainName.",
            action="Retry deploy.",
        )
    stage_records.append(_stage_ok("cloudfront", f"Created CloudFront distribution `{dist_id}`."))
    return dist_id, domain_name


def _wait_for_cloudfront_deployed(
    *,
    config: _ResolvedAWSDeployConfig,
    distribution_id: str,
    stage_records: list[dict[str, object]],
) -> None:
    deadline = monotonic() + 30 * 60
    while monotonic() < deadline:
        described = run_aws_json(["cloudfront", "get-distribution", "--id", distribution_id])
        if not described.ok or not isinstance(described.value, dict):
            raise DeployStageError(
                stage="cloudfront_wait_deployed",
                message=described.message or "Unable to describe CloudFront distribution.",
                action="Verify cloudfront:GetDistribution permissions.",
            )
        dist = described.value.get("Distribution")
        if not isinstance(dist, dict):
            raise DeployStageError(
                stage="cloudfront_wait_deployed",
                message="CloudFront get-distribution response missing Distribution payload.",
                action="Retry deploy.",
            )
        status = _read_dict_string(dist, "Status") or "UNKNOWN"
        if status.upper() == "DEPLOYED":
            stage_records.append(_stage_ok("cloudfront_wait_deployed", f"CloudFront distribution `{distribution_id}` is deployed."))
            return
        time.sleep(15)
    raise DeployStageError(
        stage="cloudfront_wait_deployed",
        message=f"Timed out waiting for CloudFront distribution `{distribution_id}` to deploy.",
        action="Inspect CloudFront deployment status and retry.",
    )


def _ensure_ecs_execution_role(*, stage_records: list[dict[str, object]]) -> str:
    role = run_aws_json(["iam", "get-role", "--role-name", ECS_EXECUTION_ROLE_NAME])
    if role.ok and isinstance(role.value, dict):
        role_payload = role.value.get("Role")
        if isinstance(role_payload, dict):
            role_arn = _read_dict_string(role_payload, "Arn")
            if role_arn:
                stage_records.append(_stage_ok("ecs_execution_role", f"IAM role `{ECS_EXECUTION_ROLE_NAME}` is ready."))
                return role_arn
    lowered = (role.message or "").lower()
    if "nosuchentity" not in lowered and "not found" not in lowered:
        raise DeployStageError(
            stage="ecs_execution_role",
            message=role.message or "Unable to inspect ECS execution role.",
            action="Verify IAM permissions for iam:GetRole.",
        )
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    created = run_aws_json(
        [
            "iam",
            "create-role",
            "--role-name",
            ECS_EXECUTION_ROLE_NAME,
            "--assume-role-policy-document",
            _to_json_argument(trust_policy),
        ]
    )
    if not created.ok or not isinstance(created.value, dict):
        raise DeployStageError(
            stage="ecs_execution_role",
            message=created.message or "Unable to create ECS execution role.",
            action="Grant iam:CreateRole and retry.",
        )
    attach = run_aws_json(
        [
            "iam",
            "attach-role-policy",
            "--role-name",
            ECS_EXECUTION_ROLE_NAME,
            "--policy-arn",
            "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
        ]
    )
    if not attach.ok:
        raise DeployStageError(
            stage="ecs_execution_role",
            message=attach.message or "Unable to attach ECS execution policy.",
            action="Grant iam:AttachRolePolicy and retry.",
        )
    role_payload = created.value.get("Role")
    if not isinstance(role_payload, dict):
        raise DeployStageError(
            stage="ecs_execution_role",
            message="ECS execution role response missing Role payload.",
            action="Retry deploy.",
        )
    role_arn = _read_dict_string(role_payload, "Arn")
    if not role_arn:
        raise DeployStageError(
            stage="ecs_execution_role",
            message="Unable to resolve ECS execution role ARN.",
            action="Retry deploy.",
        )
    stage_records.append(_stage_ok("ecs_execution_role", f"Created IAM role `{ECS_EXECUTION_ROLE_NAME}`."))
    return role_arn


def _ensure_ecs_task_role(
    *,
    config: _ResolvedAWSDeployConfig,
    stage_records: list[dict[str, object]],
) -> str:
    role_name = f"{config.app_name}-{ECS_TASK_ROLE_SUFFIX}"
    role = run_aws_json(["iam", "get-role", "--role-name", role_name])
    if role.ok and isinstance(role.value, dict):
        role_payload = role.value.get("Role")
        if isinstance(role_payload, dict):
            role_arn = _read_dict_string(role_payload, "Arn")
            if role_arn:
                _put_ecs_task_role_policy(role_name=role_name, bucket_name=config.bucket_name)
                stage_records.append(_stage_ok("ecs_task_role", f"IAM role `{role_name}` is ready."))
                return role_arn
    lowered = (role.message or "").lower()
    if "nosuchentity" not in lowered and "not found" not in lowered:
        raise DeployStageError(
            stage="ecs_task_role",
            message=role.message or "Unable to inspect ECS task role.",
            action="Verify IAM permissions for iam:GetRole.",
        )
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    created = run_aws_json(
        [
            "iam",
            "create-role",
            "--role-name",
            role_name,
            "--assume-role-policy-document",
            _to_json_argument(trust_policy),
        ]
    )
    if not created.ok or not isinstance(created.value, dict):
        raise DeployStageError(
            stage="ecs_task_role",
            message=created.message or "Unable to create ECS task role.",
            action="Grant iam:CreateRole and retry.",
        )
    _put_ecs_task_role_policy(role_name=role_name, bucket_name=config.bucket_name)
    role_payload = created.value.get("Role")
    if not isinstance(role_payload, dict):
        raise DeployStageError(
            stage="ecs_task_role",
            message="ECS task role response missing Role payload.",
            action="Retry deploy.",
        )
    role_arn = _read_dict_string(role_payload, "Arn")
    if not role_arn:
        raise DeployStageError(
            stage="ecs_task_role",
            message="Unable to resolve ECS task role ARN.",
            action="Retry deploy.",
        )
    stage_records.append(_stage_ok("ecs_task_role", f"Created IAM role `{role_name}`."))
    return role_arn


def _put_ecs_task_role_policy(*, role_name: str, bucket_name: str) -> None:
    policy_document = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:ListBucket"],
                "Resource": [f"arn:aws:s3:::{bucket_name}"],
            },
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                "Resource": [f"arn:aws:s3:::{bucket_name}/*"],
            },
        ],
    }
    put_policy = run_aws_json(
        [
            "iam",
            "put-role-policy",
            "--role-name",
            role_name,
            "--policy-name",
            ECS_TASK_INLINE_POLICY_NAME,
            "--policy-document",
            _to_json_argument(policy_document),
        ]
    )
    if not put_policy.ok:
        raise DeployStageError(
            stage="ecs_task_role",
            message=put_policy.message or "Unable to apply ECS task inline policy.",
            action="Grant iam:PutRolePolicy and retry.",
        )


def _ensure_ecs_log_group(
    *,
    config: _ResolvedAWSDeployConfig,
    stage_records: list[dict[str, object]],
) -> str:
    log_group_name = f"/ecs/{config.app_name}"
    described = run_aws_json(
        [
            "logs",
            "describe-log-groups",
            "--region",
            config.region,
            "--log-group-name-prefix",
            log_group_name,
        ]
    )
    if not described.ok or not isinstance(described.value, dict):
        raise DeployStageError(
            stage="ecs_log_group",
            message=described.message or "Unable to inspect CloudWatch log groups.",
            action="Verify logs:DescribeLogGroups permissions.",
        )
    groups = described.value.get("logGroups")
    if isinstance(groups, list):
        for group in groups:
            if not isinstance(group, dict):
                continue
            if _read_dict_string(group, "logGroupName") == log_group_name:
                stage_records.append(_stage_ok("ecs_log_group", f"Log group `{log_group_name}` is ready."))
                return log_group_name
    created = run_aws_json(
        [
            "logs",
            "create-log-group",
            "--region",
            config.region,
            "--log-group-name",
            log_group_name,
        ]
    )
    if not created.ok:
        raise DeployStageError(
            stage="ecs_log_group",
            message=created.message or "Unable to create CloudWatch log group.",
            action="Verify logs:CreateLogGroup permissions.",
        )
    stage_records.append(_stage_ok("ecs_log_group", f"Created log group `{log_group_name}`."))
    return log_group_name


def _ensure_ecs_cluster(
    *,
    config: _ResolvedAWSDeployConfig,
    stage_records: list[dict[str, object]],
) -> str:
    cluster_name = f"{config.app_name}-cluster"
    described = run_aws_json(
        [
            "ecs",
            "describe-clusters",
            "--region",
            config.region,
            "--clusters",
            cluster_name,
        ]
    )
    if described.ok and isinstance(described.value, dict):
        clusters = described.value.get("clusters")
        if isinstance(clusters, list) and clusters and isinstance(clusters[0], dict):
            status = _read_dict_string(clusters[0], "status")
            if status and status.upper() != "INACTIVE":
                stage_records.append(_stage_ok("ecs_cluster", f"ECS cluster `{cluster_name}` is ready."))
                return cluster_name
    created = run_aws_json(
        [
            "ecs",
            "create-cluster",
            "--region",
            config.region,
            "--cluster-name",
            cluster_name,
        ]
    )
    if not created.ok:
        raise DeployStageError(
            stage="ecs_cluster",
            message=created.message or "Unable to create ECS cluster.",
            action="Verify ecs:CreateCluster permissions.",
        )
    stage_records.append(_stage_ok("ecs_cluster", f"Created ECS cluster `{cluster_name}`."))
    return cluster_name


def _ensure_ecs_service_linked_role(*, stage_records: list[dict[str, object]]) -> None:
    role = run_aws_json(["iam", "get-role", "--role-name", ECS_SERVICE_LINKED_ROLE_NAME])
    if role.ok:
        stage_records.append(
            _stage_ok("ecs_service_linked_role", f"IAM service-linked role `{ECS_SERVICE_LINKED_ROLE_NAME}` is ready.")
        )
        return
    lowered = (role.message or "").lower()
    if "nosuchentity" not in lowered and "not found" not in lowered:
        raise DeployStageError(
            stage="ecs_service_linked_role",
            message=role.message or "Unable to inspect ECS service-linked role.",
            action="Verify iam:GetRole permissions.",
        )
    created = run_aws_json(
        [
            "iam",
            "create-service-linked-role",
            "--aws-service-name",
            "ecs.amazonaws.com",
        ]
    )
    if not created.ok:
        created_lowered = (created.message or "").lower()
        if "has been taken in this account" not in created_lowered and "already exists" not in created_lowered:
            raise DeployStageError(
                stage="ecs_service_linked_role",
                message=created.message or "Unable to create ECS service-linked role.",
                action="Grant iam:CreateServiceLinkedRole permissions or create AWSServiceRoleForECS manually.",
            )
    stage_records.append(
        _stage_ok("ecs_service_linked_role", f"Created IAM service-linked role `{ECS_SERVICE_LINKED_ROLE_NAME}`.")
    )


def _register_task_definition(
    *,
    config: _ResolvedAWSDeployConfig,
    runtime_env: OrderedDict[str, str],
    execution_role_arn: str,
    task_role_arn: str,
    log_group_name: str,
    stage_records: list[dict[str, object]],
) -> str:
    family = f"{config.app_name}-task"
    container_def = {
        "name": "portworld",
        "image": config.image_uri,
        "essential": True,
        "portMappings": [{"containerPort": 8080, "protocol": "tcp"}],
        "environment": [{"name": key, "value": value} for key, value in runtime_env.items()],
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": log_group_name,
                "awslogs-region": config.region,
                "awslogs-stream-prefix": "ecs",
            },
        },
    }
    registered = run_aws_json(
        [
            "ecs",
            "register-task-definition",
            "--region",
            config.region,
            "--family",
            family,
            "--requires-compatibilities",
            "FARGATE",
            "--network-mode",
            "awsvpc",
            "--cpu",
            ECS_TASK_CPU,
            "--memory",
            ECS_TASK_MEMORY,
            "--execution-role-arn",
            execution_role_arn,
            "--task-role-arn",
            task_role_arn,
            "--container-definitions",
            _to_json_argument([container_def]),
        ]
    )
    if not registered.ok or not isinstance(registered.value, dict):
        raise DeployStageError(
            stage="ecs_task_definition",
            message=registered.message or "Unable to register ECS task definition.",
            action="Verify ecs:RegisterTaskDefinition and iam:PassRole permissions.",
        )
    task_def = registered.value.get("taskDefinition")
    if not isinstance(task_def, dict):
        raise DeployStageError(
            stage="ecs_task_definition",
            message="Task definition registration response missing payload.",
            action="Retry deploy.",
        )
    task_definition_arn = _read_dict_string(task_def, "taskDefinitionArn")
    if not task_definition_arn:
        raise DeployStageError(
            stage="ecs_task_definition",
            message="Task definition registration did not return an ARN.",
            action="Retry deploy.",
        )
    stage_records.append(_stage_ok("ecs_task_definition", f"Registered task definition `{task_definition_arn}`."))
    return task_definition_arn


def _upsert_ecs_service(
    *,
    config: _ResolvedAWSDeployConfig,
    cluster_name: str,
    task_definition_arn: str,
    subnet_ids: tuple[str, ...],
    ecs_security_group_id: str,
    target_group_arn: str,
    stage_records: list[dict[str, object]],
) -> str:
    service_name = config.app_name
    described = run_aws_json(
        [
            "ecs",
            "describe-services",
            "--region",
            config.region,
            "--cluster",
            cluster_name,
            "--services",
            service_name,
        ]
    )
    exists = False
    if described.ok and isinstance(described.value, dict):
        services = described.value.get("services")
        if isinstance(services, list) and services and isinstance(services[0], dict):
            status = _read_dict_string(services[0], "status")
            exists = bool(status and status.upper() != "INACTIVE")

    network_conf = {
        "awsvpcConfiguration": {
            "subnets": list(subnet_ids),
            "securityGroups": [ecs_security_group_id],
            "assignPublicIp": "ENABLED",
        }
    }
    lb_conf = [
        {
            "targetGroupArn": target_group_arn,
            "containerName": "portworld",
            "containerPort": 8080,
        }
    ]
    if exists:
        updated = run_aws_json(
            [
                "ecs",
                "update-service",
                "--region",
                config.region,
                "--cluster",
                cluster_name,
                "--service",
                service_name,
                "--task-definition",
                task_definition_arn,
                "--force-new-deployment",
            ]
        )
        if not updated.ok:
            raise DeployStageError(
                stage="ecs_service",
                message=updated.message or "Unable to update ECS service.",
                action="Verify ecs:UpdateService permissions.",
            )
        stage_records.append(_stage_ok("ecs_service", f"Updated ECS service `{service_name}`."))
        return service_name

    created = run_aws_json(
        [
            "ecs",
            "create-service",
            "--region",
            config.region,
            "--cluster",
            cluster_name,
            "--service-name",
            service_name,
            "--task-definition",
            task_definition_arn,
            "--desired-count",
            "1",
            "--launch-type",
            "FARGATE",
            "--network-configuration",
            _to_json_argument(network_conf),
            "--load-balancers",
            _to_json_argument(lb_conf),
            "--health-check-grace-period-seconds",
            "60",
        ]
    )
    if not created.ok:
        raise DeployStageError(
            stage="ecs_service",
            message=created.message or "Unable to create ECS service.",
            action="Verify ecs:CreateService permissions and load balancer configuration.",
        )
    stage_records.append(_stage_ok("ecs_service", f"Created ECS service `{service_name}`."))
    return service_name


def _wait_for_ecs_service_stable(
    *,
    config: _ResolvedAWSDeployConfig,
    cluster_name: str,
    service_name: str,
    expected_task_definition_arn: str,
    stage_records: list[dict[str, object]],
) -> None:
    deadline = monotonic() + 20 * 60
    while monotonic() < deadline:
        described = run_aws_json(
            [
                "ecs",
                "describe-services",
                "--region",
                config.region,
                "--cluster",
                cluster_name,
                "--services",
                service_name,
            ]
        )
        if not described.ok or not isinstance(described.value, dict):
            raise DeployStageError(
                stage="ecs_service_wait_stable",
                message=described.message or "Unable to inspect ECS service state.",
                action="Verify ecs:DescribeServices permissions and inspect ECS service events/tasks.",
            )
        services = described.value.get("services")
        if not isinstance(services, list) or not services or not isinstance(services[0], dict):
            raise DeployStageError(
                stage="ecs_service_wait_stable",
                message="ECS service describe response did not include the target service.",
                action="Inspect ECS cluster/service state and retry.",
            )
        service = services[0]
        status = (_read_dict_string(service, "status") or "UNKNOWN").upper()
        running_count = service.get("runningCount")
        pending_count = service.get("pendingCount")
        desired_count = service.get("desiredCount")
        deployments = service.get("deployments")
        if status == "ACTIVE" and isinstance(running_count, int) and isinstance(pending_count, int) and isinstance(desired_count, int):
            if (
                running_count >= desired_count
                and pending_count == 0
                and _ecs_expected_task_definition_ready(
                    deployments,
                    expected_task_definition_arn=expected_task_definition_arn,
                )
            ):
                stage_records.append(_stage_ok("ecs_service_wait_stable", f"ECS service `{service_name}` is stable."))
                return
        time.sleep(10)
    raise DeployStageError(
        stage="ecs_service_wait_stable",
        message="ECS service did not reach a stable state before timeout.",
        action="Inspect ECS service deployments, events, and task health before retrying.",
    )


def _ecs_expected_task_definition_ready(
    deployments: object,
    *,
    expected_task_definition_arn: str,
) -> bool:
    if not isinstance(deployments, list) or len(deployments) == 0:
        return False
    for deployment in deployments:
        if not isinstance(deployment, dict):
            return False
        status = (_read_dict_string(deployment, "status") or "").upper()
        rollout_state = (_read_dict_string(deployment, "rolloutState") or "").upper()
        task_definition_arn = _read_dict_string(deployment, "taskDefinition")
        desired_count = deployment.get("desiredCount")
        running_count = deployment.get("runningCount")
        pending_count = deployment.get("pendingCount")
        if task_definition_arn != expected_task_definition_arn:
            continue
        if status not in {"PRIMARY", "ACTIVE"}:
            continue
        if rollout_state not in {"COMPLETED", "IN_PROGRESS"}:
            return False
        if not (
            isinstance(desired_count, int)
            and isinstance(running_count, int)
            and isinstance(pending_count, int)
        ):
            return False
        if running_count < 1:
            return False
        return pending_count == 0
    return False


def _upsert_apprunner_service(
    config: _ResolvedAWSDeployConfig,
    *,
    access_role_arn: str,
    instance_role_arn: str,
    runtime_env: OrderedDict[str, str],
    stage_records: list[dict[str, object]],
) -> tuple[str, str]:
    service_arn = _find_apprunner_service_arn(region=config.region, service_name=config.app_name)
    source_configuration = _build_apprunner_source_configuration(
        image_uri=config.image_uri,
        access_role_arn=access_role_arn,
        runtime_env=runtime_env,
    )
    health_check = {
        "Protocol": "TCP",
        "Interval": 10,
        "Timeout": 5,
        "HealthyThreshold": 1,
        "UnhealthyThreshold": 5,
    }
    instance_configuration = {
        "Cpu": APP_RUNNER_INSTANCE_CPU,
        "Memory": APP_RUNNER_INSTANCE_MEMORY,
        "InstanceRoleArn": instance_role_arn,
    }
    if service_arn is None:
        created = run_aws_json(
            [
                "apprunner",
                "create-service",
                "--region",
                config.region,
                "--service-name",
                config.app_name,
                "--source-configuration",
                _to_json_argument(source_configuration),
                "--instance-configuration",
                _to_json_argument(instance_configuration),
                "--health-check-configuration",
                _to_json_argument(health_check),
            ]
        )
        if not created.ok or not isinstance(created.value, dict):
            raise DeployStageError(
                stage="apprunner_service",
                message=created.message or "Unable to create App Runner service.",
                action="Verify apprunner:CreateService permissions.",
            )
        service = created.value.get("Service")
        if not isinstance(service, dict):
            raise DeployStageError(
                stage="apprunner_service",
                message="App Runner create-service response missing Service payload.",
                action="Retry deploy.",
            )
        service_arn = _read_dict_string(service, "ServiceArn")
        if not service_arn:
            raise DeployStageError(
                stage="apprunner_service",
                message="Unable to resolve App Runner service ARN.",
                action="Retry deploy.",
            )
        stage_records.append(_stage_ok("apprunner_service", f"Created App Runner service `{config.app_name}`."))
    else:
        updated = run_aws_json(
            [
                "apprunner",
                "update-service",
                "--region",
                config.region,
                "--service-arn",
                service_arn,
                "--source-configuration",
                _to_json_argument(source_configuration),
                "--instance-configuration",
                _to_json_argument(instance_configuration),
                "--health-check-configuration",
                _to_json_argument(health_check),
            ]
        )
        if not updated.ok:
            raise DeployStageError(
                stage="apprunner_service",
                message=updated.message or "Unable to update App Runner service.",
                action="Verify apprunner:UpdateService permissions.",
            )
        stage_records.append(_stage_ok("apprunner_service", f"Updated App Runner service `{config.app_name}`."))

    service_url = _wait_for_apprunner_running(
        region=config.region,
        service_arn=service_arn,
        stage_records=stage_records,
    )
    return service_arn, service_url


def _build_apprunner_source_configuration(
    *,
    image_uri: str,
    access_role_arn: str,
    runtime_env: OrderedDict[str, str],
) -> dict[str, object]:
    return {
        "AuthenticationConfiguration": {
            "AccessRoleArn": access_role_arn,
        },
        "AutoDeploymentsEnabled": False,
        "ImageRepository": {
            "ImageIdentifier": image_uri,
            "ImageRepositoryType": "ECR",
            "ImageConfiguration": {
                "Port": "8080",
                "RuntimeEnvironmentVariables": dict(runtime_env),
            },
        },
    }


def _find_apprunner_service_arn(*, region: str, service_name: str) -> str | None:
    listed = run_aws_json(["apprunner", "list-services", "--region", region])
    if not listed.ok or not isinstance(listed.value, dict):
        return None
    summaries = listed.value.get("ServiceSummaryList")
    if not isinstance(summaries, list):
        return None
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        if _read_dict_string(summary, "ServiceName") == service_name:
            return _read_dict_string(summary, "ServiceArn")
    return None


def _wait_for_apprunner_running(
    *,
    region: str,
    service_arn: str,
    stage_records: list[dict[str, object]],
) -> str:
    deadline = monotonic() + 20 * 60
    while monotonic() < deadline:
        described = run_aws_json(
            ["apprunner", "describe-service", "--region", region, "--service-arn", service_arn]
        )
        if not described.ok or not isinstance(described.value, dict):
            raise DeployStageError(
                stage="apprunner_service_wait_running",
                message=described.message or "Unable to describe App Runner service.",
                action="Verify apprunner:DescribeService permissions.",
            )
        service = described.value.get("Service")
        if not isinstance(service, dict):
            raise DeployStageError(
                stage="apprunner_service_wait_running",
                message="App Runner describe-service response missing Service payload.",
                action="Retry deploy.",
            )
        status = _read_dict_string(service, "Status") or "UNKNOWN"
        if status == "RUNNING":
            raw_service_url = _read_dict_string(service, "ServiceUrl")
            if not raw_service_url:
                raise DeployStageError(
                    stage="apprunner_service_wait_running",
                    message="App Runner service reached RUNNING but service URL is missing.",
                    action="Inspect App Runner service details in AWS console.",
                )
            service_url = _normalize_service_url(raw_service_url)
            stage_records.append(_stage_ok("apprunner_service_wait_running", f"App Runner service is RUNNING: {service_url}"))
            return service_url
        if status.endswith("FAILED") or status in {"DELETED"}:
            raise DeployStageError(
                stage="apprunner_service_wait_running",
                message=f"App Runner service entered terminal status `{status}`.",
                action="Inspect App Runner operation events/logs and retry.",
            )
        time.sleep(8)

    raise DeployStageError(
        stage="apprunner_service_wait_running",
        message="Timed out waiting for App Runner service to become RUNNING.",
        action="Inspect App Runner service status and logs.",
    )


def _normalize_service_url(value: str) -> str:
    text = value.strip()
    if text.startswith("https://"):
        return text
    if text.startswith("http://"):
        return "https://" + text[len("http://") :]
    return f"https://{text}"


def _stage_ok(stage: str, message: str) -> dict[str, object]:
    return {"stage": stage, "status": "ok", "message": message}


def _to_json_argument(payload: object) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _probe_livez(service_url: str) -> bool:
    url = service_url.rstrip("/") + "/livez"
    try:
        response = httpx.get(url, timeout=15.0)
    except Exception:
        return False
    return response.status_code == 200


def _probe_ws(service_url: str, bearer_token: str) -> bool:
    parsed = urlparse(service_url)
    host = parsed.hostname
    if host is None:
        return False
    port = parsed.port or 443
    headers = {
        "Host": host,
        "Connection": "Upgrade",
        "Upgrade": "websocket",
        "Sec-WebSocket-Version": "13",
        "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
    }
    token = bearer_token.strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        raw_response = _tls_http_get_upgrade(
            host=host,
            port=port,
            path="/ws/session",
            headers=headers,
            timeout=10.0,
        )
    except Exception:
        return False
    status_code = _parse_http_status_code(raw_response)
    return status_code in {101, 401}


def _wait_for_public_validation(service_url: str, bearer_token: str) -> tuple[bool, bool]:
    livez_ok = False
    ws_ok = False
    deadline = monotonic() + 300.0
    while monotonic() < deadline:
        if not livez_ok:
            livez_ok = _probe_livez(service_url)
        if livez_ok and not ws_ok:
            ws_ok = _probe_ws(service_url, bearer_token)
        if livez_ok and ws_ok:
            return True, True
        time.sleep(3)
    return livez_ok, ws_ok


def _tls_http_get_upgrade(
    *,
    host: str,
    port: int,
    path: str,
    headers: dict[str, str],
    timeout: float,
) -> str:
    request_lines = [f"GET {path} HTTP/1.1", *(f"{key}: {value}" for key, value in headers.items()), "", ""]
    request = "\r\n".join(request_lines).encode("ascii", errors="ignore")
    context = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=timeout) as tcp_sock:
        with context.wrap_socket(tcp_sock, server_hostname=host) as tls_sock:
            tls_sock.settimeout(timeout)
            tls_sock.sendall(request)
            chunks: list[bytes] = []
            deadline = monotonic() + timeout
            while monotonic() < deadline:
                data = tls_sock.recv(4096)
                if not data:
                    break
                chunks.append(data)
                if b"\r\n\r\n" in b"".join(chunks):
                    break
            return b"".join(chunks).decode("iso-8859-1", errors="replace")


def _parse_http_status_code(raw_response: str) -> int | None:
    status_line = raw_response.splitlines()[0] if raw_response else ""
    parts = status_line.split(" ")
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def _sanitize_runtime_env_for_output(runtime_env: OrderedDict[str, str]) -> OrderedDict[str, str]:
    redacted: OrderedDict[str, str] = OrderedDict()
    for key, value in runtime_env.items():
        upper = key.upper()
        if (
            key in {"BACKEND_DATABASE_URL", "DATABASE_URL"}
            or "TOKEN" in upper
            or "SECRET" in upper
            or "PASSWORD" in upper
            or upper.endswith("_KEY")
        ):
            redacted[key] = "***REDACTED***"
        else:
            redacted[key] = value
    return redacted


def _confirm_mutations(cli_context: CLIContext, config: _ResolvedAWSDeployConfig) -> None:
    if cli_context.non_interactive or cli_context.yes:
        return
    confirmed = click.confirm(
        "\n".join(
            [
                "Proceed with AWS ECS/Fargate deploy and managed infrastructure provisioning?",
                f"account_id: {config.account_id}",
                f"region: {config.region}",
                f"ecs_service: {config.app_name}",
                f"bucket: {config.bucket_name}",
                f"rds_instance: {config.rds_instance_identifier}",
                f"image_uri: {config.image_uri}",
            ]
        ),
        default=True,
        show_default=True,
    )
    if not confirmed:
        raise click.Abort()


def _require_value(cli_context: CLIContext, *, value: str | None, prompt: str, error: str) -> str:
    normalized = normalize_optional_text(value)
    if normalized is not None:
        return normalized
    if cli_context.non_interactive:
        raise DeployUsageError(error)
    prompted = click.prompt(prompt, default="", show_default=False)
    normalized = normalize_optional_text(prompted)
    if normalized is None:
        raise DeployUsageError(error)
    return normalized


def _first_non_empty(*values: str | None) -> str | None:
    for value in values:
        normalized = normalize_optional_text(value)
        if normalized is not None:
            return normalized
    return None


def _read_dict_string(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _generate_rds_password() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(28))


def _normalize_rds_identifier(value: str) -> str:
    lowered = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    compact = []
    for ch in lowered:
        if ch == "-" and compact and compact[-1] == "-":
            continue
        compact.append(ch)
    normalized = "".join(compact).strip("-")
    if not normalized:
        normalized = "portworld-pg"
    if not normalized[0].isalpha():
        normalized = "p" + normalized
    return normalized[:63]


def _now_ms() -> int:
    return time_ns() // 1_000_000
