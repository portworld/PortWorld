from __future__ import annotations

from collections import OrderedDict
from time import monotonic
import time

from portworld_cli.aws.common import run_aws_json
from portworld_cli.aws.constants import (
    ECS_EXECUTION_ROLE_NAME,
    ECS_SERVICE_LINKED_ROLE_NAME,
    ECS_TASK_CPU,
    ECS_TASK_INLINE_POLICY_NAME,
    ECS_TASK_MEMORY,
    ECS_TASK_ROLE_SUFFIX,
)
from portworld_cli.aws.stages.config import ResolvedAWSDeployConfig
from portworld_cli.aws.stages.shared import read_dict_string, stage_ok, to_json_argument
from portworld_cli.deploy.config import DeployStageError


def build_runtime_env_vars(
    env_values: OrderedDict[str, str],
    config: ResolvedAWSDeployConfig,
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
    final_env["PORT"] = "8080"
    return final_env


def ensure_ecs_execution_role(*, stage_records: list[dict[str, object]]) -> str:
    role = run_aws_json(["iam", "get-role", "--role-name", ECS_EXECUTION_ROLE_NAME])
    if role.ok and isinstance(role.value, dict):
        role_payload = role.value.get("Role")
        if isinstance(role_payload, dict):
            role_arn = read_dict_string(role_payload, "Arn")
            if role_arn:
                stage_records.append(stage_ok("ecs_execution_role", f"IAM role `{ECS_EXECUTION_ROLE_NAME}` is ready."))
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
            to_json_argument(trust_policy),
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
    role_arn = read_dict_string(role_payload, "Arn")
    if not role_arn:
        raise DeployStageError(
            stage="ecs_execution_role",
            message="Unable to resolve ECS execution role ARN.",
            action="Retry deploy.",
        )
    stage_records.append(stage_ok("ecs_execution_role", f"Created IAM role `{ECS_EXECUTION_ROLE_NAME}`."))
    return role_arn


def ensure_ecs_task_role(
    *,
    config: ResolvedAWSDeployConfig,
    stage_records: list[dict[str, object]],
) -> str:
    role_name = f"{config.app_name}-{ECS_TASK_ROLE_SUFFIX}"
    role = run_aws_json(["iam", "get-role", "--role-name", role_name])
    if role.ok and isinstance(role.value, dict):
        role_payload = role.value.get("Role")
        if isinstance(role_payload, dict):
            role_arn = read_dict_string(role_payload, "Arn")
            if role_arn:
                put_ecs_task_role_policy(role_name=role_name, bucket_name=config.bucket_name)
                stage_records.append(stage_ok("ecs_task_role", f"IAM role `{role_name}` is ready."))
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
            to_json_argument(trust_policy),
        ]
    )
    if not created.ok or not isinstance(created.value, dict):
        raise DeployStageError(
            stage="ecs_task_role",
            message=created.message or "Unable to create ECS task role.",
            action="Grant iam:CreateRole and retry.",
        )
    put_ecs_task_role_policy(role_name=role_name, bucket_name=config.bucket_name)
    role_payload = created.value.get("Role")
    if not isinstance(role_payload, dict):
        raise DeployStageError(
            stage="ecs_task_role",
            message="ECS task role response missing Role payload.",
            action="Retry deploy.",
        )
    role_arn = read_dict_string(role_payload, "Arn")
    if not role_arn:
        raise DeployStageError(
            stage="ecs_task_role",
            message="Unable to resolve ECS task role ARN.",
            action="Retry deploy.",
        )
    stage_records.append(stage_ok("ecs_task_role", f"Created IAM role `{role_name}`."))
    return role_arn


def put_ecs_task_role_policy(*, role_name: str, bucket_name: str) -> None:
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
            to_json_argument(policy_document),
        ]
    )
    if not put_policy.ok:
        raise DeployStageError(
            stage="ecs_task_role",
            message=put_policy.message or "Unable to apply ECS task inline policy.",
            action="Grant iam:PutRolePolicy and retry.",
        )


def ensure_ecs_log_group(
    *,
    config: ResolvedAWSDeployConfig,
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
            if read_dict_string(group, "logGroupName") == log_group_name:
                stage_records.append(stage_ok("ecs_log_group", f"Log group `{log_group_name}` is ready."))
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
    stage_records.append(stage_ok("ecs_log_group", f"Created log group `{log_group_name}`."))
    return log_group_name


def ensure_ecs_cluster(
    *,
    config: ResolvedAWSDeployConfig,
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
            status = read_dict_string(clusters[0], "status")
            if status and status.upper() != "INACTIVE":
                stage_records.append(stage_ok("ecs_cluster", f"ECS cluster `{cluster_name}` is ready."))
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
    stage_records.append(stage_ok("ecs_cluster", f"Created ECS cluster `{cluster_name}`."))
    return cluster_name


def ensure_ecs_service_linked_role(*, stage_records: list[dict[str, object]]) -> None:
    role = run_aws_json(["iam", "get-role", "--role-name", ECS_SERVICE_LINKED_ROLE_NAME])
    if role.ok:
        stage_records.append(
            stage_ok("ecs_service_linked_role", f"IAM service-linked role `{ECS_SERVICE_LINKED_ROLE_NAME}` is ready.")
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
        stage_ok("ecs_service_linked_role", f"Created IAM service-linked role `{ECS_SERVICE_LINKED_ROLE_NAME}`.")
    )


def register_task_definition(
    *,
    config: ResolvedAWSDeployConfig,
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
            to_json_argument([container_def]),
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
    task_definition_arn = read_dict_string(task_def, "taskDefinitionArn")
    if not task_definition_arn:
        raise DeployStageError(
            stage="ecs_task_definition",
            message="Task definition registration did not return an ARN.",
            action="Retry deploy.",
        )
    stage_records.append(stage_ok("ecs_task_definition", f"Registered task definition `{task_definition_arn}`."))
    return task_definition_arn


def upsert_ecs_service(
    *,
    config: ResolvedAWSDeployConfig,
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
            status = read_dict_string(services[0], "status")
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
        stage_records.append(stage_ok("ecs_service", f"Updated ECS service `{service_name}`."))
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
            to_json_argument(network_conf),
            "--load-balancers",
            to_json_argument(lb_conf),
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
    stage_records.append(stage_ok("ecs_service", f"Created ECS service `{service_name}`."))
    return service_name


def wait_for_ecs_service_stable(
    *,
    config: ResolvedAWSDeployConfig,
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
        status = (read_dict_string(service, "status") or "UNKNOWN").upper()
        running_count = service.get("runningCount")
        pending_count = service.get("pendingCount")
        desired_count = service.get("desiredCount")
        deployments = service.get("deployments")
        if status == "ACTIVE" and isinstance(running_count, int) and isinstance(pending_count, int) and isinstance(desired_count, int):
            if (
                running_count >= desired_count
                and pending_count == 0
                and ecs_expected_task_definition_ready(
                    deployments,
                    expected_task_definition_arn=expected_task_definition_arn,
                )
            ):
                stage_records.append(stage_ok("ecs_service_wait_stable", f"ECS service `{service_name}` is stable."))
                return
        time.sleep(10)
    raise DeployStageError(
        stage="ecs_service_wait_stable",
        message="ECS service did not reach a stable state before timeout.",
        action="Inspect ECS service deployments, events, and task health before retrying.",
    )


def ecs_expected_task_definition_ready(
    deployments: object,
    *,
    expected_task_definition_arn: str,
) -> bool:
    if not isinstance(deployments, list) or len(deployments) == 0:
        return False
    for deployment in deployments:
        if not isinstance(deployment, dict):
            return False
        status = (read_dict_string(deployment, "status") or "").upper()
        rollout_state = (read_dict_string(deployment, "rolloutState") or "").upper()
        task_definition_arn = read_dict_string(deployment, "taskDefinition")
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
