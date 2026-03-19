from __future__ import annotations

from dataclasses import dataclass
import os

from portworld_cli.aws.common import (
    aws_cli_available,
    is_postgres_url,
    normalize_optional_text,
    run_aws_json,
    run_aws_text,
    s3_bucket_name_tls_warning,
    split_csv_values,
    validate_s3_bucket_name,
)
from portworld_cli.output import DiagnosticCheck
from portworld_cli.workspace.project_config import ProjectConfig


@dataclass(frozen=True, slots=True)
class AWSDoctorDetails:
    account_id: str | None
    arn: str | None
    region: str | None
    cluster_name: str | None
    service_name: str | None
    vpc_id: str | None
    subnet_ids: tuple[str, ...]
    certificate_arn: str | None
    bucket_name: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "account_id": self.account_id,
            "arn": self.arn,
            "region": self.region,
            "cluster_name": self.cluster_name,
            "service_name": self.service_name,
            "vpc_id": self.vpc_id,
            "subnet_ids": list(self.subnet_ids),
            "certificate_arn": self.certificate_arn,
            "bucket_name": self.bucket_name,
        }


@dataclass(frozen=True, slots=True)
class AWSDoctorEvaluation:
    ok: bool
    checks: tuple[DiagnosticCheck, ...]
    details: AWSDoctorDetails


def evaluate_aws_ecs_fargate_readiness(
    *,
    explicit_region: str | None,
    explicit_cluster: str | None,
    explicit_service: str | None,
    explicit_vpc_id: str | None,
    explicit_subnet_ids: str | None,
    explicit_certificate_arn: str | None,
    explicit_database_url: str | None,
    explicit_s3_bucket: str | None,
    env_values: dict[str, str],
    project_config: ProjectConfig | None,
) -> AWSDoctorEvaluation:
    checks: list[DiagnosticCheck] = []

    aws_defaults = None if project_config is None else project_config.deploy.aws_ecs_fargate

    region = _first_non_empty(
        explicit_region,
        None if aws_defaults is None else aws_defaults.region,
        os.environ.get("AWS_REGION"),
        os.environ.get("AWS_DEFAULT_REGION"),
    )

    cluster_name = _first_non_empty(
        explicit_cluster,
        None if aws_defaults is None else aws_defaults.cluster_name,
    )
    service_name = _first_non_empty(
        explicit_service,
        None if aws_defaults is None else aws_defaults.service_name,
    )
    vpc_id = _first_non_empty(
        explicit_vpc_id,
        None if aws_defaults is None else aws_defaults.vpc_id,
    )
    subnet_ids = _resolve_subnets(
        explicit_value=explicit_subnet_ids,
        configured=(() if aws_defaults is None else aws_defaults.subnet_ids),
    )
    certificate_arn = normalize_optional_text(explicit_certificate_arn)

    database_url = _first_non_empty(
        explicit_database_url,
        env_values.get("BACKEND_DATABASE_URL"),
    )
    bucket_name = _first_non_empty(
        explicit_s3_bucket,
        env_values.get("BACKEND_OBJECT_STORE_NAME"),
        env_values.get("BACKEND_OBJECT_STORE_BUCKET"),
    )

    cli_ok = aws_cli_available()
    checks.append(
        DiagnosticCheck(
            id="aws_cli_installed",
            status="pass" if cli_ok else "fail",
            message="aws CLI is installed" if cli_ok else "aws CLI is not installed or not on PATH.",
            action=None if cli_ok else "Install AWS CLI v2 and re-run doctor.",
        )
    )

    account_id: str | None = None
    arn: str | None = None
    if cli_ok:
        identity = run_aws_json(["sts", "get-caller-identity"])  # region not required
        if identity.ok and isinstance(identity.value, dict):
            account_id = _read_dict_string(identity.value, "Account")
            arn = _read_dict_string(identity.value, "Arn")
            checks.append(
                DiagnosticCheck(
                    id="aws_authenticated",
                    status="pass",
                    message=f"Authenticated AWS identity: {arn or 'unknown'}",
                )
            )
        else:
            checks.append(
                DiagnosticCheck(
                    id="aws_authenticated",
                    status="fail",
                    message=identity.message or "Unable to resolve AWS caller identity.",
                    action="Run `aws configure` or set AWS credentials and retry.",
                )
            )

    if region is None and cli_ok:
        configured_region = run_aws_text(["configure", "get", "region"])
        if configured_region.ok and isinstance(configured_region.value, str):
            region = normalize_optional_text(configured_region.value)

    checks.append(
        DiagnosticCheck(
            id="aws_region_selected",
            status="pass" if region else "fail",
            message=(
                f"Using AWS region '{region}'."
                if region
                else "No AWS region resolved for ECS/Fargate checks."
            ),
            action=None if region else "Pass --aws-region or set AWS_REGION/AWS_DEFAULT_REGION.",
        )
    )

    checks.extend(
        [
            _required_value_check("aws_cluster_selected", cluster_name, "--aws-cluster is required."),
            _required_value_check("aws_service_selected", service_name, "--aws-service is required."),
            _required_value_check("aws_vpc_selected", vpc_id, "--aws-vpc-id is required."),
            DiagnosticCheck(
                id="aws_subnets_selected",
                status="pass" if subnet_ids else "fail",
                message=(
                    f"Using subnet ids: {', '.join(subnet_ids)}"
                    if subnet_ids
                    else "No AWS subnets resolved."
                ),
                action=None if subnet_ids else "Pass --aws-subnet-ids (comma-separated).",
            ),
            _required_value_check(
                "aws_certificate_selected",
                certificate_arn,
                "--aws-certificate-arn is required for ALB HTTPS listener validation.",
            ),
        ]
    )

    db_ok = bool(database_url and is_postgres_url(database_url))
    checks.append(
        DiagnosticCheck(
            id="database_url_ready",
            status="pass" if db_ok else "fail",
            message=(
                "BACKEND_DATABASE_URL is present and uses a PostgreSQL scheme."
                if db_ok
                else "BACKEND_DATABASE_URL is missing or not PostgreSQL-shaped."
            ),
            action=None if db_ok else "Set BACKEND_DATABASE_URL to an existing PostgreSQL connection URL.",
        )
    )

    if bucket_name is None:
        checks.append(
            DiagnosticCheck(
                id="s3_bucket_name_valid",
                status="fail",
                message="No managed object-store bucket name resolved.",
                action="Set BACKEND_OBJECT_STORE_NAME or pass --aws-s3-bucket.",
            )
        )
    else:
        bucket_validation_error = validate_s3_bucket_name(bucket_name)
        bucket_tls_warning = s3_bucket_name_tls_warning(bucket_name)
        checks.append(
            DiagnosticCheck(
                id="s3_bucket_name_valid",
                status="pass" if bucket_validation_error is None else "fail",
                message=(
                    f"S3 bucket name '{bucket_name}' is valid."
                    if bucket_validation_error is None
                    else bucket_validation_error
                ),
                action=None if bucket_validation_error is None else "Choose a valid S3 bucket name.",
            )
        )
        checks.append(
            DiagnosticCheck(
                id="s3_bucket_name_tls_compatibility",
                status="warn" if bucket_tls_warning else "pass",
                message=bucket_tls_warning or "S3 bucket name is compatible with virtual-hosted HTTPS access.",
                action=(
                    "Use an S3 bucket name without periods if clients will use virtual-hosted HTTPS endpoints."
                    if bucket_tls_warning
                    else None
                ),
            )
        )

    if cli_ok and region and subnet_ids:
        subnet_result = run_aws_json(["ec2", "describe-subnets", "--subnet-ids", *subnet_ids, "--region", region])
        if not subnet_result.ok or not isinstance(subnet_result.value, dict):
            checks.append(
                DiagnosticCheck(
                    id="subnet_vpc_validation",
                    status="fail",
                    message=subnet_result.message or "Unable to describe subnets via AWS CLI.",
                    action="Check subnet ids and IAM permissions for ec2:DescribeSubnets.",
                )
            )
        else:
            subnets = subnet_result.value.get("Subnets")
            if not isinstance(subnets, list) or len(subnets) != len(subnet_ids):
                checks.append(
                    DiagnosticCheck(
                        id="subnet_vpc_validation",
                        status="fail",
                        message="One or more provided subnets were not found.",
                        action="Verify subnet ids in the selected AWS region.",
                    )
                )
            else:
                vpc_ids: set[str] = set()
                azs: set[str] = set()
                for subnet in subnets:
                    if isinstance(subnet, dict):
                        subnet_vpc = _read_dict_string(subnet, "VpcId")
                        subnet_az = _read_dict_string(subnet, "AvailabilityZone")
                        if subnet_vpc:
                            vpc_ids.add(subnet_vpc)
                        if subnet_az:
                            azs.add(subnet_az)
                subnet_vpc_ok = len(vpc_ids) == 1 and (vpc_id is None or vpc_id in vpc_ids)
                multi_az_ok = len(azs) >= 2
                checks.append(
                    DiagnosticCheck(
                        id="subnet_vpc_validation",
                        status="pass" if subnet_vpc_ok and multi_az_ok else "fail",
                        message=(
                            "Subnets map to the selected VPC and span at least two availability zones."
                            if subnet_vpc_ok and multi_az_ok
                            else "Subnets must belong to the selected VPC and span at least two availability zones."
                        ),
                        action=(
                            None
                            if subnet_vpc_ok and multi_az_ok
                            else "Provide subnet ids in the same VPC across at least two AZs."
                        ),
                    )
                )

    if cli_ok and region and certificate_arn:
        cert_result = run_aws_json(
            ["acm", "describe-certificate", "--certificate-arn", certificate_arn, "--region", region]
        )
        if not cert_result.ok or not isinstance(cert_result.value, dict):
            checks.append(
                DiagnosticCheck(
                    id="acm_certificate_valid",
                    status="fail",
                    message=cert_result.message or "Unable to describe ACM certificate.",
                    action="Verify certificate ARN, region, and IAM permissions for acm:DescribeCertificate.",
                )
            )
        else:
            certificate_payload = cert_result.value.get("Certificate")
            status = None
            if isinstance(certificate_payload, dict):
                status = _read_dict_string(certificate_payload, "Status")
            checks.append(
                DiagnosticCheck(
                    id="acm_certificate_valid",
                    status="pass" if status == "ISSUED" else "fail",
                    message=(
                        f"ACM certificate status is {status}."
                        if status
                        else "ACM certificate status could not be read."
                    ),
                    action=(
                        None if status == "ISSUED" else "Use an ACM certificate ARN in ISSUED state for HTTPS listener."
                    ),
                )
            )

    if cli_ok and region and cluster_name and service_name:
        service_result = run_aws_json(
            [
                "ecs",
                "describe-services",
                "--region",
                region,
                "--cluster",
                cluster_name,
                "--services",
                service_name,
            ]
        )
        if not service_result.ok or not isinstance(service_result.value, dict):
            checks.append(
                DiagnosticCheck(
                    id="ecs_service_describe",
                    status="fail",
                    message=service_result.message or "Unable to describe ECS service.",
                    action="Verify ECS cluster/service names and permissions for ecs:DescribeServices.",
                )
            )
        else:
            service_payload = _first_ecs_service(service_result.value)
            if service_payload is None:
                checks.append(
                    DiagnosticCheck(
                        id="ecs_service_describe",
                        status="fail",
                        message="ECS service was not found for the selected cluster.",
                        action="Create the ECS service or correct --aws-cluster/--aws-service.",
                    )
                )
            else:
                service_status = _read_dict_string(service_payload, "status")
                checks.append(
                    DiagnosticCheck(
                        id="ecs_service_active",
                        status="pass" if service_status == "ACTIVE" else "fail",
                        message=(
                            f"ECS service status is {service_status}."
                            if service_status
                            else "ECS service status is unavailable."
                        ),
                        action=(
                            None
                            if service_status == "ACTIVE"
                            else "Ensure the ECS service exists and is ACTIVE before deploy."
                        ),
                    )
                )

                network_ok = _service_network_alignment_ok(
                    service_payload=service_payload,
                    expected_subnets=subnet_ids,
                )
                checks.append(
                    DiagnosticCheck(
                        id="ecs_service_network_alignment",
                        status="pass" if network_ok else "fail",
                        message=(
                            "ECS service awsvpc subnets align with configured subnets."
                            if network_ok
                            else "ECS service networking does not align with configured subnets."
                        ),
                        action=(
                            None
                            if network_ok
                            else "Align ECS service awsvpc subnet configuration with --aws-subnet-ids."
                        ),
                    )
                )

                task_definition_arn = _read_dict_string(service_payload, "taskDefinition")
                if task_definition_arn:
                    task_definition_result = run_aws_json(
                        [
                            "ecs",
                            "describe-task-definition",
                            "--region",
                            region,
                            "--task-definition",
                            task_definition_arn,
                        ]
                    )
                    if not task_definition_result.ok or not isinstance(task_definition_result.value, dict):
                        checks.append(
                            DiagnosticCheck(
                                id="ecs_task_definition_describe",
                                status="fail",
                                message=task_definition_result.message or "Unable to describe task definition.",
                                action="Verify permissions for ecs:DescribeTaskDefinition.",
                            )
                        )
                    else:
                        task_definition_payload = task_definition_result.value.get("taskDefinition")
                        if not isinstance(task_definition_payload, dict):
                            checks.append(
                                DiagnosticCheck(
                                    id="ecs_task_definition_describe",
                                    status="fail",
                                    message="Task definition payload was missing.",
                                    action="Verify ECS task definition configuration.",
                                )
                            )
                        else:
                            network_mode = _read_dict_string(task_definition_payload, "networkMode")
                            compatibilities = task_definition_payload.get("requiresCompatibilities")
                            is_fargate = isinstance(compatibilities, list) and "FARGATE" in compatibilities
                            checks.append(
                                DiagnosticCheck(
                                    id="ecs_task_definition_fargate_compatible",
                                    status="pass" if network_mode == "awsvpc" and is_fargate else "fail",
                                    message=(
                                        "Task definition is Fargate compatible (networkMode=awsvpc)."
                                        if network_mode == "awsvpc" and is_fargate
                                        else "Task definition must use networkMode=awsvpc and include requiresCompatibilities=FARGATE."
                                    ),
                                    action=(
                                        None
                                        if network_mode == "awsvpc" and is_fargate
                                        else "Register a Fargate-compatible task definition and update the service."
                                    ),
                                )
                            )
                            execution_role = _read_dict_string(task_definition_payload, "executionRoleArn")
                            task_role = _read_dict_string(task_definition_payload, "taskRoleArn")
                            checks.append(
                                DiagnosticCheck(
                                    id="ecs_execution_role_present",
                                    status="pass" if execution_role else "fail",
                                    message=(
                                        f"Execution role resolved: {execution_role}"
                                        if execution_role
                                        else "Execution role ARN is missing."
                                    ),
                                    action=(
                                        None
                                        if execution_role
                                        else "Set executionRoleArn with ECR/CloudWatch/Secrets permissions."
                                    ),
                                )
                            )
                            checks.append(
                                DiagnosticCheck(
                                    id="ecs_task_role_present",
                                    status="pass" if task_role else "warn",
                                    message=(
                                        f"Task role resolved: {task_role}"
                                        if task_role
                                        else "Task role ARN is missing."
                                    ),
                                    action=(
                                        None
                                        if task_role
                                        else "Set taskRoleArn when runtime AWS API access is required."
                                    ),
                                )
                            )
                            has_secret_refs = _task_definition_has_secret_refs(task_definition_payload)
                            checks.append(
                                DiagnosticCheck(
                                    id="ecs_secrets_model_ready",
                                    status="pass" if has_secret_refs else "warn",
                                    message=(
                                        "ECS task definition includes secret references."
                                        if has_secret_refs
                                        else "No ECS secret references were detected in container definitions."
                                    ),
                                    action=(
                                        None
                                        if has_secret_refs
                                        else "Use ECS secrets mappings (for example Secrets Manager references) for sensitive values."
                                    ),
                                )
                            )
                else:
                    checks.append(
                        DiagnosticCheck(
                            id="ecs_task_definition_describe",
                            status="fail",
                            message="ECS service did not report a task definition ARN.",
                            action="Ensure the ECS service references a valid task definition.",
                        )
                    )

    details = AWSDoctorDetails(
        account_id=account_id,
        arn=arn,
        region=region,
        cluster_name=cluster_name,
        service_name=service_name,
        vpc_id=vpc_id,
        subnet_ids=subnet_ids,
        certificate_arn=certificate_arn,
        bucket_name=bucket_name,
    )
    return AWSDoctorEvaluation(
        ok=all(check.status != "fail" for check in checks),
        checks=tuple(checks),
        details=details,
    )


def _required_value_check(check_id: str, value: str | None, action: str) -> DiagnosticCheck:
    return DiagnosticCheck(
        id=check_id,
        status="pass" if value else "fail",
        message=f"Resolved value: {value}" if value else "Required value is missing.",
        action=None if value else action,
    )


def _resolve_subnets(*, explicit_value: str | None, configured: tuple[str, ...]) -> tuple[str, ...]:
    from_explicit = split_csv_values(explicit_value)
    if from_explicit:
        return from_explicit
    return tuple(value for value in configured if normalize_optional_text(value) is not None)


def _read_dict_string(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _first_non_empty(*values: str | None) -> str | None:
    for value in values:
        normalized = normalize_optional_text(value)
        if normalized is not None:
            return normalized
    return None


def _first_ecs_service(payload: dict[str, object]) -> dict[str, object] | None:
    services = payload.get("services")
    if not isinstance(services, list) or not services:
        return None
    first = services[0]
    if not isinstance(first, dict):
        return None
    return first


def _service_network_alignment_ok(*, service_payload: dict[str, object], expected_subnets: tuple[str, ...]) -> bool:
    if not expected_subnets:
        return True
    network_configuration = service_payload.get("networkConfiguration")
    if not isinstance(network_configuration, dict):
        return False
    awsvpc_configuration = network_configuration.get("awsvpcConfiguration")
    if not isinstance(awsvpc_configuration, dict):
        return False
    subnets = awsvpc_configuration.get("subnets")
    if not isinstance(subnets, list):
        return False
    resolved_subnets = {value for value in subnets if isinstance(value, str)}
    return set(expected_subnets).issubset(resolved_subnets)


def _task_definition_has_secret_refs(task_definition_payload: dict[str, object]) -> bool:
    container_definitions = task_definition_payload.get("containerDefinitions")
    if not isinstance(container_definitions, list):
        return False
    for container in container_definitions:
        if not isinstance(container, dict):
            continue
        secrets = container.get("secrets")
        if isinstance(secrets, list) and len(secrets) > 0:
            return True
    return False
