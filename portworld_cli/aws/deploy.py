from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
import socket
import ssl
import subprocess
from time import monotonic
from time import time_ns
from urllib.parse import urlparse

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


@dataclass(frozen=True, slots=True)
class DeployAWSECSFargateOptions:
    region: str | None
    cluster: str | None
    service: str | None
    vpc_id: str | None
    subnet_ids: str | None
    certificate_arn: str | None
    database_url: str | None
    bucket: str | None
    alb_url: str | None
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
    cluster_name: str
    service_name: str
    vpc_id: str
    subnet_ids: tuple[str, ...]
    certificate_arn: str
    database_url: str
    bucket_name: str
    alb_url: str
    ecr_repository: str
    image_tag: str
    image_uri: str
    cors_origins: str
    allowed_hosts: str
    published_release_tag: str | None
    published_image_ref: str | None


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
                "cluster_name": config.cluster_name,
                "service_name": config.service_name,
                "bucket_name": config.bucket_name,
                "alb_url": config.alb_url,
                "image_uri": config.image_uri,
            }
        )

        _run_aws_deploy_mutations(config, env_values=env_values, stage_records=stage_records, project_root=session.workspace_root)

        livez_ok = _probe_livez(config.alb_url)
        ws_ok = _probe_ws(config.alb_url, env_values.get("BACKEND_BEARER_TOKEN", ""))
        if not livez_ok:
            raise DeployStageError(
                stage="post_deploy_validation",
                message="ALB HTTPS endpoint did not return 200 from /livez.",
                action="Verify ALB listener, target group health checks, and backend container readiness.",
            )
        if not ws_ok:
            raise DeployStageError(
                stage="post_deploy_validation",
                message="ALB HTTPS endpoint did not complete /ws/session websocket handshake.",
                action="Verify ALB websocket compatibility and Authorization header forwarding.",
            )

        write_deploy_state(
            session.workspace_paths.state_file_for_target(TARGET_AWS_ECS_FARGATE),
            DeployState(
                project_id=config.account_id,
                region=config.region,
                service_name=config.service_name,
                runtime_source=config.runtime_source,
                image_source_mode=config.image_source_mode,
                artifact_repository=config.ecr_repository,
                artifact_repository_base=config.ecr_repository,
                cloud_sql_instance=None,
                database_name="external",
                bucket_name=config.bucket_name,
                image=config.image_uri,
                published_release_tag=config.published_release_tag,
                published_image_ref=config.published_image_ref,
                service_url=config.alb_url,
                service_account_email=None,
                last_deployed_at_ms=_now_ms(),
            ),
        )

        message_lines = [
            f"target: {TARGET_AWS_ECS_FARGATE}",
            f"account_id: {config.account_id}",
            f"region: {config.region}",
            f"cluster_name: {config.cluster_name}",
            f"service_name: {config.service_name}",
            f"alb_url: {config.alb_url}",
            f"image_source_mode: {config.image_source_mode}",
            f"image_uri: {config.image_uri}",
            f"bucket_name: {config.bucket_name}",
            "next_steps:",
            f"- curl {config.alb_url.rstrip('/')}/livez",
            f"- portworld doctor --target aws-ecs-fargate --aws-region {config.region}",
        ]
        return CommandResult(
            ok=True,
            command=COMMAND_NAME,
            message="\n".join(message_lines),
            data={
                "target": TARGET_AWS_ECS_FARGATE,
                "region": config.region,
                "cluster_name": config.cluster_name,
                "service_name": config.service_name,
                "service_url": config.alb_url,
                "image": config.image_uri,
                "runtime_source": config.runtime_source,
                "image_source_mode": config.image_source_mode,
                "published_release_tag": config.published_release_tag,
                "published_image_ref": config.published_image_ref,
                "resources": resources,
                "stages": stage_records,
                "runtime_env": _sanitize_runtime_env_for_output(_build_runtime_env_vars(env_values, config)),
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
    cluster_name = _require_value(
        cli_context,
        value=_first_non_empty(options.cluster, aws_defaults.cluster_name),
        prompt="AWS ECS cluster name",
        error="AWS ECS cluster name is required.",
    )
    service_name = _require_value(
        cli_context,
        value=_first_non_empty(options.service, aws_defaults.service_name),
        prompt="AWS ECS service name",
        error="AWS ECS service name is required.",
    )
    vpc_id = _require_value(
        cli_context,
        value=_first_non_empty(options.vpc_id, aws_defaults.vpc_id),
        prompt="AWS VPC id",
        error="AWS VPC id is required.",
    )
    subnet_ids = split_csv_values(options.subnet_ids) or tuple(aws_defaults.subnet_ids)
    if not subnet_ids:
        raise DeployUsageError("AWS subnet ids are required (--subnet-ids).")

    certificate_arn = _require_value(
        cli_context,
        value=options.certificate_arn,
        prompt="ACM certificate ARN",
        error="ACM certificate ARN is required for AWS deploy.",
    )
    database_url = _require_value(
        cli_context,
        value=_first_non_empty(options.database_url, env_values.get("BACKEND_DATABASE_URL")),
        prompt="Managed PostgreSQL URL",
        error="BACKEND_DATABASE_URL (existing Postgres) is required for AWS deploy.",
    )
    if not is_postgres_url(database_url):
        raise DeployUsageError("BACKEND_DATABASE_URL must use postgres:// or postgresql://.")

    bucket_name = _first_non_empty(
        options.bucket,
        env_values.get("BACKEND_OBJECT_STORE_NAME"),
        env_values.get("BACKEND_OBJECT_STORE_BUCKET"),
        f"{service_name}-artifacts",
    )
    assert bucket_name is not None
    bucket_error = validate_s3_bucket_name(bucket_name)
    if bucket_error:
        raise DeployUsageError(bucket_error)

    alb_url = _require_value(
        cli_context,
        value=options.alb_url,
        prompt="ALB HTTPS URL",
        error="ALB HTTPS URL is required for AWS deploy.",
    )
    parsed_url = urlparse(alb_url)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise DeployUsageError("--alb-url must be an https URL.")

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

    ecr_repository = _first_non_empty(options.ecr_repo, f"{service_name}-backend")
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

    return _ResolvedAWSDeployConfig(
        runtime_source=runtime_source,
        image_source_mode=image_source_mode,
        account_id=account_id,
        region=region,
        cluster_name=cluster_name,
        service_name=service_name,
        vpc_id=vpc_id,
        subnet_ids=subnet_ids,
        certificate_arn=certificate_arn,
        database_url=database_url,
        bucket_name=bucket_name,
        alb_url=alb_url,
        ecr_repository=ecr_repository,
        image_tag=image_tag,
        image_uri=image_uri,
        cors_origins=cors_origins or "*",
        allowed_hosts=allowed_hosts or "*",
        published_release_tag=published_release_tag,
        published_image_ref=published_image_ref,
    )


def _build_runtime_env_vars(env_values: OrderedDict[str, str], config: _ResolvedAWSDeployConfig) -> OrderedDict[str, str]:
    final_env: OrderedDict[str, str] = OrderedDict()
    excluded = {
        "BACKEND_DATA_DIR",
        "BACKEND_SQLITE_PATH",
        "BACKEND_STORAGE_BACKEND",
        "BACKEND_OBJECT_STORE_PROVIDER",
        "BACKEND_OBJECT_STORE_NAME",
        "BACKEND_OBJECT_STORE_BUCKET",
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
    final_env["BACKEND_OBJECT_STORE_BUCKET"] = config.bucket_name
    final_env["BACKEND_OBJECT_STORE_PREFIX"] = config.service_name
    final_env["BACKEND_DATABASE_URL"] = config.database_url
    final_env["CORS_ORIGINS"] = config.cors_origins
    final_env["BACKEND_ALLOWED_HOSTS"] = config.allowed_hosts
    return final_env


def _run_aws_deploy_mutations(
    config: _ResolvedAWSDeployConfig,
    *,
    env_values: OrderedDict[str, str],
    stage_records: list[dict[str, object]],
    project_root: Path,
) -> None:
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
    _rollout_ecs_service(config, env_values=env_values, stage_records=stage_records)


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


def _rollout_ecs_service(
    config: _ResolvedAWSDeployConfig,
    *,
    env_values: OrderedDict[str, str],
    stage_records: list[dict[str, object]],
) -> None:
    describe_services = run_aws_json(
        [
            "ecs",
            "describe-services",
            "--region",
            config.region,
            "--cluster",
            config.cluster_name,
            "--services",
            config.service_name,
        ]
    )
    if not describe_services.ok or not isinstance(describe_services.value, dict):
        raise DeployStageError(
            stage="ecs_service_describe",
            message=describe_services.message or "Unable to describe ECS service.",
            action="Verify ECS cluster/service names and IAM access.",
        )
    service = _extract_single_service(describe_services.value, config)
    task_definition_arn = _read_dict_string(service, "taskDefinition")
    if task_definition_arn is None:
        raise DeployStageError(
            stage="ecs_service_describe",
            message="ECS service did not return a task definition ARN.",
            action="Ensure ECS service is ACTIVE and configured.",
        )
    stage_records.append(_stage_ok("ecs_service_describe", f"Loaded ECS service `{config.service_name}`."))

    describe_task_definition = run_aws_json(
        [
            "ecs",
            "describe-task-definition",
            "--region",
            config.region,
            "--task-definition",
            task_definition_arn,
        ]
    )
    if not describe_task_definition.ok or not isinstance(describe_task_definition.value, dict):
        raise DeployStageError(
            stage="task_definition_describe",
            message=describe_task_definition.message or "Unable to describe ECS task definition.",
            action="Verify ECS task definition permissions.",
        )
    current_task_definition = describe_task_definition.value.get("taskDefinition")
    if not isinstance(current_task_definition, dict):
        raise DeployStageError(
            stage="task_definition_describe",
            message="ECS task definition response was missing `taskDefinition`.",
            action="Verify ECS API access and retry.",
        )

    runtime_env = _build_runtime_env_vars(env_values, config)
    new_task_definition_payload = _build_task_definition_registration_payload(
        config,
        task_definition=current_task_definition,
        runtime_env=runtime_env,
    )
    register = run_aws_json(
        [
            "ecs",
            "register-task-definition",
            "--region",
            config.region,
            "--cli-input-json",
            _to_json_argument(new_task_definition_payload),
        ]
    )
    if not register.ok or not isinstance(register.value, dict):
        raise DeployStageError(
            stage="task_definition_register",
            message=register.message or "Unable to register updated task definition.",
            action="Verify ECS task definition IAM permissions and payload validity.",
        )
    registered = register.value.get("taskDefinition")
    if not isinstance(registered, dict):
        raise DeployStageError(
            stage="task_definition_register",
            message="ECS register-task-definition response missing taskDefinition.",
            action="Verify ECS API output format and retry.",
        )
    new_task_definition_arn = _read_dict_string(registered, "taskDefinitionArn")
    if new_task_definition_arn is None:
        raise DeployStageError(
            stage="task_definition_register",
            message="Registered task definition ARN could not be resolved.",
            action="Verify ECS register-task-definition response and retry.",
        )
    stage_records.append(_stage_ok("task_definition_register", f"Registered `{new_task_definition_arn}`."))

    update = run_aws_json(
        [
            "ecs",
            "update-service",
            "--region",
            config.region,
            "--cluster",
            config.cluster_name,
            "--service",
            config.service_name,
            "--task-definition",
            new_task_definition_arn,
            "--force-new-deployment",
        ]
    )
    if not update.ok:
        raise DeployStageError(
            stage="ecs_service_update",
            message=update.message or "Unable to update ECS service to new task definition.",
            action="Verify ECS service update permissions and service health.",
        )
    stage_records.append(_stage_ok("ecs_service_update", f"Updated service `{config.service_name}` deployment."))

    wait_result = run_aws_json(
        [
            "ecs",
            "wait",
            "services-stable",
            "--region",
            config.region,
            "--cluster",
            config.cluster_name,
            "--services",
            config.service_name,
        ]
    )
    if not wait_result.ok:
        raise DeployStageError(
            stage="ecs_service_wait_stable",
            message=wait_result.message or "ECS service did not become stable.",
            action="Inspect ECS events and task logs, then retry deploy.",
        )
    stage_records.append(_stage_ok("ecs_service_wait_stable", f"Service `{config.service_name}` is stable."))


def _extract_single_service(payload: dict[str, object], config: _ResolvedAWSDeployConfig) -> dict[str, object]:
    services = payload.get("services")
    if not isinstance(services, list):
        raise DeployStageError(
            stage="ecs_service_describe",
            message="ECS describe-services response missing `services` list.",
            action="Verify ECS API permissions and retry.",
        )
    if not services:
        raise DeployStageError(
            stage="ecs_service_describe",
            message=f"ECS service `{config.service_name}` was not found in cluster `{config.cluster_name}`.",
            action="Create the ECS service before deploy or correct --cluster/--service values.",
        )
    first = services[0]
    if not isinstance(first, dict):
        raise DeployStageError(
            stage="ecs_service_describe",
            message="ECS service payload format was invalid.",
            action="Verify ECS API output and retry.",
        )
    return first


def _build_task_definition_registration_payload(
    config: _ResolvedAWSDeployConfig,
    *,
    task_definition: dict[str, object],
    runtime_env: OrderedDict[str, str],
) -> dict[str, object]:
    container_definitions = task_definition.get("containerDefinitions")
    if not isinstance(container_definitions, list) or not container_definitions:
        raise DeployStageError(
            stage="task_definition_register",
            message="Existing task definition did not include container definitions.",
            action="Update ECS task definition to include container definitions and retry.",
        )

    selected_index = 0
    for index, candidate in enumerate(container_definitions):
        if isinstance(candidate, dict) and _read_dict_string(candidate, "name") == config.service_name:
            selected_index = index
            break

    updated_definitions: list[dict[str, object]] = []
    for index, raw_definition in enumerate(container_definitions):
        if not isinstance(raw_definition, dict):
            raise DeployStageError(
                stage="task_definition_register",
                message="Container definition format was invalid.",
                action="Ensure ECS task definition container definitions are valid JSON objects.",
            )
        definition = dict(raw_definition)
        if index == selected_index:
            definition["image"] = config.image_uri
            definition["environment"] = [
                {"name": key, "value": value}
                for key, value in runtime_env.items()
            ]
        updated_definitions.append(definition)

    payload: dict[str, object] = {
        "family": task_definition.get("family"),
        "networkMode": task_definition.get("networkMode"),
        "containerDefinitions": updated_definitions,
    }
    _copy_task_definition_key(task_definition, payload, "taskRoleArn")
    _copy_task_definition_key(task_definition, payload, "executionRoleArn")
    _copy_task_definition_key(task_definition, payload, "volumes")
    _copy_task_definition_key(task_definition, payload, "placementConstraints")
    _copy_task_definition_key(task_definition, payload, "requiresCompatibilities")
    _copy_task_definition_key(task_definition, payload, "cpu")
    _copy_task_definition_key(task_definition, payload, "memory")
    _copy_task_definition_key(task_definition, payload, "tags")
    _copy_task_definition_key(task_definition, payload, "pidMode")
    _copy_task_definition_key(task_definition, payload, "ipcMode")
    _copy_task_definition_key(task_definition, payload, "proxyConfiguration")
    _copy_task_definition_key(task_definition, payload, "inferenceAccelerators")
    _copy_task_definition_key(task_definition, payload, "ephemeralStorage")
    _copy_task_definition_key(task_definition, payload, "runtimePlatform")

    if not isinstance(payload["family"], str) or not payload["family"]:
        raise DeployStageError(
            stage="task_definition_register",
            message="Existing task definition family could not be resolved.",
            action="Verify ECS task definition is valid and retry.",
        )
    return payload


def _copy_task_definition_key(source: dict[str, object], target: dict[str, object], key: str) -> None:
    value = source.get(key)
    if value is None:
        return
    target[key] = value


def _to_json_argument(payload: dict[str, object]) -> str:
    import json

    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _stage_ok(stage: str, message: str) -> dict[str, object]:
    return {"stage": stage, "status": "ok", "message": message}


def _probe_livez(alb_url: str) -> bool:
    try:
        response = httpx.get(f"{alb_url.rstrip('/')}/livez", timeout=10.0)
    except Exception:
        return False
    return response.status_code == 200


def _probe_ws(alb_url: str, bearer_token: str | None) -> bool:
    parsed = urlparse(alb_url)
    if parsed.scheme != "https" or parsed.hostname is None:
        return False
    host = parsed.hostname
    port = parsed.port or 443
    headers = {
        "Host": host,
        "Connection": "Upgrade",
        "Upgrade": "websocket",
        "Sec-WebSocket-Version": "13",
        "Sec-WebSocket-Key": "cG9ydHdvcmxkLWF3cy12MS0xMjM0NQ==",
    }
    token = normalize_optional_text(bearer_token)
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
                "Proceed with AWS ECS/Fargate deploy recording and validation?",
                f"account_id: {config.account_id}",
                f"region: {config.region}",
                f"cluster: {config.cluster_name}",
                f"service: {config.service_name}",
                f"alb_url: {config.alb_url}",
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


def _now_ms() -> int:
    return time_ns() // 1_000_000
