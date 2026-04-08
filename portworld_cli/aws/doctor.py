from __future__ import annotations

from dataclasses import dataclass
import os

from portworld_cli.aws.client import AWSAdapters
from portworld_cli.aws.common import (
    aws_cli_available,
    normalize_optional_text,
    s3_bucket_name_tls_warning,
    split_csv_values,
    validate_s3_bucket_name,
)
from portworld_cli.output import DiagnosticCheck
from portworld_cli.workspace.project_config import RUNTIME_SOURCE_PUBLISHED
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
    bucket_name: str | None
    ecr_repository: str | None
    alb_dns_name: str | None
    cloudfront_distribution_id: str | None
    cloudfront_domain_name: str | None
    service_url: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "account_id": self.account_id,
            "arn": self.arn,
            "region": self.region,
            "cluster_name": self.cluster_name,
            "service_name": self.service_name,
            "vpc_id": self.vpc_id,
            "subnet_ids": list(self.subnet_ids),
            "bucket_name": self.bucket_name,
            "ecr_repository": self.ecr_repository,
            "alb_dns_name": self.alb_dns_name,
            "cloudfront_distribution_id": self.cloudfront_distribution_id,
            "cloudfront_domain_name": self.cloudfront_domain_name,
            "service_url": self.service_url,
        }


@dataclass(frozen=True, slots=True)
class AWSDoctorEvaluation:
    ok: bool
    checks: tuple[DiagnosticCheck, ...]
    details: AWSDoctorDetails


def evaluate_aws_ecs_fargate_readiness(
    *,
    runtime_source: str | None,
    explicit_region: str | None,
    explicit_service: str | None,
    explicit_vpc_id: str | None,
    explicit_subnet_ids: str | None,
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
    service_name = _first_non_empty(
        explicit_service,
        None if aws_defaults is None else aws_defaults.service_name,
    )
    cluster_name = None if service_name is None else f"{service_name}-cluster"
    vpc_id = _first_non_empty(
        explicit_vpc_id,
        None if aws_defaults is None else aws_defaults.vpc_id,
    )
    subnet_ids = _resolve_subnets(
        explicit_value=explicit_subnet_ids,
        configured=(() if aws_defaults is None else aws_defaults.subnet_ids),
    )
    _unused_database_url = _first_non_empty(
        explicit_database_url,
        env_values.get("BACKEND_DATABASE_URL"),
    )
    bucket_name = _first_non_empty(
        explicit_s3_bucket,
        env_values.get("BACKEND_OBJECT_STORE_NAME"),
        None if service_name is None else f"{service_name}-memory",
    )
    ecr_repository = None
    if runtime_source != RUNTIME_SOURCE_PUBLISHED and service_name is not None:
        ecr_repository = f"{service_name}-backend"
    alb_name = None if service_name is None else f"{service_name}-alb"[:32]
    cloudfront_comment = None if service_name is None else f"PortWorld managed {service_name}"

    cli_ok = aws_cli_available()
    checks.append(
        DiagnosticCheck(
            id="aws_cli_installed",
            status="pass" if cli_ok else "fail",
            message="aws CLI is installed" if cli_ok else "aws CLI is not installed or not on PATH.",
            action=None if cli_ok else "Install AWS CLI v2 and retry doctor.",
        )
    )

    account_id: str | None = None
    arn: str | None = None
    aws_adapters = AWSAdapters.create() if cli_ok else None
    if cli_ok:
        assert aws_adapters is not None
        identity = aws_adapters.compute.run_json(["sts", "get-caller-identity"])
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
        assert aws_adapters is not None
        configured_region = aws_adapters.executor.run_text(["configure", "get", "region"])
        if configured_region.ok and isinstance(configured_region.value, str):
            region = normalize_optional_text(configured_region.value)

    checks.append(
        DiagnosticCheck(
            id="aws_region_selected",
            status="pass" if region else "fail",
            message=f"Using AWS region '{region}'." if region else "No AWS region resolved.",
            action=None if region else "Pass --aws-region or set AWS_REGION/AWS_DEFAULT_REGION.",
        )
    )
    checks.append(
        DiagnosticCheck(
            id="ecs_service_selected",
            status="pass" if service_name else "fail",
            message=f"Using AWS ECS service '{service_name}'." if service_name else "No AWS ECS service name resolved.",
            action=None if service_name else "Pass --aws-service or configure the AWS managed target first.",
        )
    )
    checks.append(
        DiagnosticCheck(
            id="ecs_cluster_selected",
            status="pass" if cluster_name else "fail",
            message=f"Using ECS cluster '{cluster_name}'." if cluster_name else "No ECS cluster name could be derived.",
            action=None if cluster_name else "Pass --aws-service or configure the AWS managed target first.",
        )
    )

    if _unused_database_url is not None:
        checks.append(
            DiagnosticCheck(
                id="database_url_ignored",
                status="warn",
                message="`BACKEND_DATABASE_URL` is set but AWS managed deploy no longer uses database URLs.",
                action="Remove database URL settings from workspace config if they are no longer needed.",
            )
        )
    checks.extend(_build_runtime_contract_checks(env_values))
    checks.extend(_build_production_posture_checks(env_values=env_values, project_config=project_config))

    if bucket_name is None:
        checks.append(
            DiagnosticCheck(
                id="s3_bucket_name_valid",
                status="fail",
                message="No managed object-store bucket name resolved.",
                action="Pass --aws-s3-bucket or configure the AWS target first.",
            )
        )
    else:
        bucket_validation_error = validate_s3_bucket_name(bucket_name)
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
        bucket_tls_warning = s3_bucket_name_tls_warning(bucket_name)
        checks.append(
            DiagnosticCheck(
                id="s3_bucket_name_tls_compatibility",
                status="warn" if bucket_tls_warning else "pass",
                message=bucket_tls_warning or "S3 bucket name is compatible with virtual-hosted HTTPS access.",
                action=(
                    "Prefer a bucket name without periods if clients will use virtual-hosted HTTPS endpoints."
                    if bucket_tls_warning
                    else None
                ),
            )
        )

    alb_dns_name: str | None = None
    cloudfront_distribution_id: str | None = None
    cloudfront_domain_name: str | None = None
    service_url: str | None = None
    if cli_ok and region and bucket_name:
        assert aws_adapters is not None
        checks.append(_s3_bucket_ready_check(adapters=aws_adapters, region=region, bucket_name=bucket_name))
    if cli_ok and region and ecr_repository:
        assert aws_adapters is not None
        checks.append(_ecr_repository_ready_check(adapters=aws_adapters, region=region, repository_name=ecr_repository))
    if cli_ok and region and cluster_name and service_name:
        assert aws_adapters is not None
        ecs_check, service_url = _ecs_service_check(
            adapters=aws_adapters,
            region=region,
            cluster_name=cluster_name,
            service_name=service_name,
        )
        checks.append(ecs_check)
    if cli_ok and region and alb_name:
        assert aws_adapters is not None
        alb_check, alb_dns_name = _alb_check(adapters=aws_adapters, region=region, alb_name=alb_name)
        checks.append(alb_check)
    if cli_ok and cloudfront_comment:
        assert aws_adapters is not None
        cloudfront_check, cloudfront_distribution_id, cloudfront_domain_name = _cloudfront_distribution_check(
            adapters=aws_adapters,
            comment=cloudfront_comment
        )
        checks.append(cloudfront_check)
        if service_url is None and cloudfront_domain_name is not None:
            service_url = _normalize_service_url(cloudfront_domain_name)

    details = AWSDoctorDetails(
        account_id=account_id,
        arn=arn,
        region=region,
        cluster_name=cluster_name,
        service_name=service_name,
        vpc_id=vpc_id,
        subnet_ids=subnet_ids,
        bucket_name=bucket_name,
        ecr_repository=ecr_repository,
        alb_dns_name=alb_dns_name,
        cloudfront_distribution_id=cloudfront_distribution_id,
        cloudfront_domain_name=cloudfront_domain_name,
        service_url=service_url,
    )
    return AWSDoctorEvaluation(
        ok=all(check.status != "fail" for check in checks),
        checks=tuple(checks),
        details=details,
    )


def _s3_bucket_ready_check(*, adapters: AWSAdapters, region: str, bucket_name: str) -> DiagnosticCheck:
    result = adapters.storage.run_text(["s3api", "head-bucket", "--bucket", bucket_name, "--region", region])
    if result.ok:
        return DiagnosticCheck(
            id="s3_bucket_ready",
            status="pass",
            message=f"S3 bucket '{bucket_name}' is accessible.",
        )
    lowered = _lower_message(result.message)
    if _message_indicates_not_found(lowered):
        return DiagnosticCheck(
            id="s3_bucket_ready",
            status="warn",
            message=f"S3 bucket '{bucket_name}' does not exist yet and will be created on deploy.",
            action="No action needed for first deploy if create permissions are available.",
        )
    return DiagnosticCheck(
        id="s3_bucket_ready",
        status="fail",
        message=result.message or "Unable to inspect S3 bucket.",
        action="Verify S3 permissions and bucket ownership.",
    )


def _ecr_repository_ready_check(*, adapters: AWSAdapters, region: str, repository_name: str) -> DiagnosticCheck:
    result = adapters.image.run_json(
        [
            "ecr",
            "describe-repositories",
            "--region",
            region,
            "--repository-names",
            repository_name,
        ]
    )
    if result.ok:
        return DiagnosticCheck(
            id="ecr_repository_ready",
            status="pass",
            message=f"ECR repository '{repository_name}' is ready.",
        )
    lowered = _lower_message(result.message)
    if _message_indicates_not_found(lowered):
        return DiagnosticCheck(
            id="ecr_repository_ready",
            status="warn",
            message=f"ECR repository '{repository_name}' does not exist yet and will be created on deploy.",
            action="No action needed for first deploy if ECR create permissions are available.",
        )
    return DiagnosticCheck(
        id="ecr_repository_ready",
        status="fail",
        message=result.message or "Unable to inspect ECR repository.",
        action="Verify ECR permissions.",
    )


def _ecs_service_check(
    *,
    adapters: AWSAdapters,
    region: str,
    cluster_name: str,
    service_name: str,
) -> tuple[DiagnosticCheck, str | None]:
    described_cluster = adapters.compute.run_json(
        [
            "ecs",
            "describe-clusters",
            "--region",
            region,
            "--clusters",
            cluster_name,
        ]
    )
    if not described_cluster.ok or not isinstance(described_cluster.value, dict):
        return (
            DiagnosticCheck(
                id="ecs_service_ready",
                status="fail",
                message=described_cluster.message or "Unable to inspect ECS cluster.",
                action="Verify ECS permissions and cluster visibility.",
            ),
            None,
        )
    clusters = described_cluster.value.get("clusters")
    if not isinstance(clusters, list) or not clusters or not isinstance(clusters[0], dict):
        return (
            DiagnosticCheck(
                id="ecs_service_ready",
                status="warn",
                message=f"ECS cluster '{cluster_name}' does not exist yet and will be created on deploy.",
                action="No action needed for first deploy if ECS create permissions are available.",
            ),
            None,
        )

    described_service = adapters.compute.run_json(
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
    if not described_service.ok or not isinstance(described_service.value, dict):
        return (
            DiagnosticCheck(
                id="ecs_service_ready",
                status="fail",
                message=described_service.message or "Unable to inspect ECS service.",
                action="Verify ECS permissions and service visibility.",
            ),
            None,
        )
    services = described_service.value.get("services")
    if not isinstance(services, list) or not services or not isinstance(services[0], dict):
        return (
            DiagnosticCheck(
                id="ecs_service_ready",
                status="warn",
                message=f"ECS service '{service_name}' does not exist yet and will be created on deploy.",
                action="No action needed for first deploy if ECS create permissions are available.",
            ),
            None,
        )
    service = services[0]
    status = (_read_dict_string(service, "status") or "UNKNOWN").upper()
    running_count = service.get("runningCount")
    desired_count = service.get("desiredCount")
    if status == "ACTIVE" and isinstance(running_count, int) and isinstance(desired_count, int):
        if desired_count == 0 or running_count >= desired_count:
            return (
                DiagnosticCheck(
                    id="ecs_service_ready",
                    status="pass",
                    message=f"ECS service '{service_name}' is ACTIVE with {running_count}/{desired_count} running tasks.",
                ),
                None,
            )
        return (
            DiagnosticCheck(
                id="ecs_service_ready",
                status="warn",
                message=f"ECS service '{service_name}' is ACTIVE with {running_count}/{desired_count} running tasks.",
                action="Deploy can continue, but the current ECS rollout is not fully healthy yet.",
            ),
            None,
        )
    if status == "INACTIVE":
        return (
            DiagnosticCheck(
                id="ecs_service_ready",
                status="warn",
                message=f"ECS service '{service_name}' is inactive and will be recreated on deploy.",
                action="No action needed if ECS create permissions are available.",
            ),
            None,
        )
    return (
        DiagnosticCheck(
            id="ecs_service_ready",
            status="warn",
            message=f"ECS service '{service_name}' exists with status {status}.",
            action="Deploy can continue, but existing ECS operations may still be in progress.",
        ),
        None,
    )


def _alb_check(*, adapters: AWSAdapters, region: str, alb_name: str) -> tuple[DiagnosticCheck, str | None]:
    described = adapters.network.run_json(
        [
            "elbv2",
            "describe-load-balancers",
            "--region",
            region,
            "--names",
            alb_name,
        ]
    )
    if not described.ok or not isinstance(described.value, dict):
        lowered = _lower_message(described.message)
        if _message_indicates_not_found(lowered):
            return (
                DiagnosticCheck(
                    id="alb_ready",
                    status="warn",
                    message=f"ALB '{alb_name}' does not exist yet and will be created on deploy.",
                    action="No action needed for first deploy if ELB create permissions are available.",
                ),
                None,
            )
        return (
            DiagnosticCheck(
                id="alb_ready",
                status="fail",
                message=described.message or "Unable to inspect ALB state.",
                action="Verify ELB permissions.",
            ),
            None,
        )
    load_balancers = described.value.get("LoadBalancers")
    if not isinstance(load_balancers, list) or not load_balancers or not isinstance(load_balancers[0], dict):
        return (
            DiagnosticCheck(
                id="alb_ready",
                status="warn",
                message=f"ALB '{alb_name}' does not exist yet and will be created on deploy.",
                action="No action needed for first deploy if ELB create permissions are available.",
            ),
            None,
        )
    dns_name = _read_dict_string(load_balancers[0], "DNSName")
    return (
        DiagnosticCheck(
            id="alb_ready",
            status="pass",
            message=f"ALB '{alb_name}' is ready.",
        ),
        dns_name,
    )


def _cloudfront_distribution_check(
    *,
    adapters: AWSAdapters,
    comment: str,
) -> tuple[DiagnosticCheck, str | None, str | None]:
    listed = adapters.network.run_json(["cloudfront", "list-distributions"])
    if not listed.ok or not isinstance(listed.value, dict):
        return (
            DiagnosticCheck(
                id="cloudfront_ready",
                status="fail",
                message=listed.message or "Unable to inspect CloudFront distributions.",
                action="Verify CloudFront permissions.",
            ),
            None,
            None,
        )
    distribution_list = listed.value.get("DistributionList")
    items = distribution_list.get("Items") if isinstance(distribution_list, dict) else None
    if not isinstance(items, list):
        items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if _read_dict_string(item, "Comment") != comment:
            continue
        distribution_id = _read_dict_string(item, "Id")
        domain_name = _read_dict_string(item, "DomainName")
        status = (_read_dict_string(item, "Status") or "UNKNOWN").upper()
        if status == "DEPLOYED":
            return (
                DiagnosticCheck(
                    id="cloudfront_ready",
                    status="pass",
                    message=f"CloudFront distribution '{distribution_id}' is DEPLOYED.",
                ),
                distribution_id,
                domain_name,
            )
        return (
            DiagnosticCheck(
                id="cloudfront_ready",
                status="warn",
                message=f"CloudFront distribution '{distribution_id}' exists with status {status}.",
                action="Deploy can continue, but the existing CloudFront distribution is still updating.",
            ),
            distribution_id,
            domain_name,
        )
    return (
        DiagnosticCheck(
            id="cloudfront_ready",
            status="warn",
            message="CloudFront distribution does not exist yet and will be created on deploy.",
            action="No action needed for first deploy if CloudFront create permissions are available.",
        ),
        None,
        None,
    )


def _rds_provisioning_checks(
    *,
    adapters: AWSAdapters,
    region: str,
    explicit_vpc_id: str | None,
    explicit_subnet_ids: tuple[str, ...],
    rds_instance_identifier: str,
) -> list[DiagnosticCheck]:
    checks: list[DiagnosticCheck] = []
    checks.append(
        DiagnosticCheck(
            id="rds_instance_name_ready",
            status="pass",
            message=f"RDS instance identifier '{rds_instance_identifier}' is ready for one-click provisioning.",
        )
    )
    checks.extend(
        _vpc_and_subnet_checks(
            adapters=adapters,
            region=region,
            explicit_vpc_id=explicit_vpc_id,
            explicit_subnet_ids=explicit_subnet_ids,
        )
    )

    described = adapters.database.run_json(
        [
            "rds",
            "describe-db-instances",
            "--region",
            region,
            "--db-instance-identifier",
            rds_instance_identifier,
        ]
    )
    if described.ok and isinstance(described.value, dict):
        status = _extract_db_status(described.value)
        checks.append(
            DiagnosticCheck(
                id="rds_instance_ready",
                status="pass" if status == "available" else "warn",
                message=(
                    f"RDS instance '{rds_instance_identifier}' exists with status {status}."
                    if status
                    else f"RDS instance '{rds_instance_identifier}' exists."
                ),
                action=None if status == "available" else "Deploy can continue, but the existing RDS instance is not yet available.",
            )
        )
        return checks

    lowered = _lower_message(described.message)
    if _message_indicates_not_found(lowered):
        checks.append(
            DiagnosticCheck(
                id="rds_instance_ready",
                status="warn",
                message=f"RDS instance '{rds_instance_identifier}' does not exist yet and will be created on deploy.",
                action="No action needed for first deploy if RDS create permissions and quotas are available.",
            )
        )
        return checks

    checks.append(
        DiagnosticCheck(
            id="rds_instance_ready",
            status="fail",
            message=described.message or "Unable to inspect RDS instance.",
            action="Verify RDS permissions.",
        )
    )
    return checks


def _vpc_and_subnet_checks(
    *,
    adapters: AWSAdapters,
    region: str,
    explicit_vpc_id: str | None,
    explicit_subnet_ids: tuple[str, ...],
) -> list[DiagnosticCheck]:
    checks: list[DiagnosticCheck] = []
    resolved_vpc_id = explicit_vpc_id
    if resolved_vpc_id is None:
        vpcs = adapters.network.run_json(
            [
                "ec2",
                "describe-vpcs",
                "--region",
                region,
                "--filters",
                "Name=isDefault,Values=true",
            ]
        )
        if not vpcs.ok or not isinstance(vpcs.value, dict):
            return [
                DiagnosticCheck(
                    id="rds_network_ready",
                    status="fail",
                    message=vpcs.message or "Unable to resolve default VPC for RDS provisioning.",
                    action="Verify EC2 permissions or pass --aws-vpc-id/--aws-subnet-ids.",
                )
            ]
        items = vpcs.value.get("Vpcs")
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            return [
                DiagnosticCheck(
                    id="rds_network_ready",
                    status="fail",
                    message="No default VPC was found for RDS provisioning.",
                    action="Pass --aws-vpc-id and --aws-subnet-ids or create a default VPC.",
                )
            ]
        resolved_vpc_id = _read_dict_string(items[0], "VpcId")

    checks.append(
        DiagnosticCheck(
            id="rds_vpc_ready",
            status="pass" if resolved_vpc_id else "fail",
            message=f"Using VPC '{resolved_vpc_id}' for RDS provisioning." if resolved_vpc_id else "No VPC available for RDS provisioning.",
            action=None if resolved_vpc_id else "Pass --aws-vpc-id or create a default VPC.",
        )
    )

    subnet_ids = explicit_subnet_ids
    if resolved_vpc_id and not subnet_ids:
        subnets = adapters.network.run_json(
            [
                "ec2",
                "describe-subnets",
                "--region",
                region,
                "--filters",
                f"Name=vpc-id,Values={resolved_vpc_id}",
                "Name=default-for-az,Values=true",
            ]
        )
        if not subnets.ok or not isinstance(subnets.value, dict):
            return checks + [
                DiagnosticCheck(
                    id="rds_subnets_ready",
                    status="fail",
                    message=subnets.message or "Unable to resolve default subnets for RDS provisioning.",
                    action="Verify EC2 permissions or pass --aws-subnet-ids.",
                )
            ]
        subnet_ids = _select_subnets_for_rds(subnets.value)

    checks.append(
        DiagnosticCheck(
            id="rds_subnets_ready",
            status="pass" if len(subnet_ids) >= 2 else "fail",
            message=(
                f"Using RDS subnets: {', '.join(subnet_ids)}"
                if len(subnet_ids) >= 2
                else "RDS provisioning requires at least two subnets in distinct availability zones."
            ),
            action=None if len(subnet_ids) >= 2 else "Pass --aws-subnet-ids with at least two subnets across different AZs.",
        )
    )
    return checks


def _resolve_subnets(*, explicit_value: str | None, configured: tuple[str, ...]) -> tuple[str, ...]:
    from_explicit = split_csv_values(explicit_value)
    if from_explicit:
        return from_explicit
    return tuple(value for value in configured if normalize_optional_text(value) is not None)


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


def _extract_db_status(payload: dict[str, object]) -> str | None:
    instances = payload.get("DBInstances")
    if not isinstance(instances, list) or not instances or not isinstance(instances[0], dict):
        return None
    return _read_dict_string(instances[0], "DBInstanceStatus")


def _normalize_service_url(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if text.startswith("https://"):
        return text
    if text.startswith("http://"):
        return "https://" + text[len("http://") :]
    return f"https://{text}"


def _normalize_rds_identifier(value: str) -> str:
    lowered = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    compact: list[str] = []
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


def _message_indicates_not_found(lowered_message: str) -> bool:
    return any(
        token in lowered_message
        for token in (
            "not found",
            "does not exist",
            "dbinstancenotfound",
            "repositorynotfoundexception",
            "loadbalancernotfound",
            "404",
        )
    )


def _lower_message(message: str | None) -> str:
    return (message or "").strip().lower()


def _build_runtime_contract_checks(env_values: dict[str, str]) -> list[DiagnosticCheck]:
    object_store_provider = _first_non_empty(env_values.get("BACKEND_OBJECT_STORE_PROVIDER"))
    return [
        DiagnosticCheck(
            id="managed_object_store_provider_contract",
            status="pass" if object_store_provider == "s3" else "warn",
            message=(
                "BACKEND_OBJECT_STORE_PROVIDER is set to s3."
                if object_store_provider == "s3"
                else "BACKEND_OBJECT_STORE_PROVIDER is not set to s3 in the current workspace config."
            ),
            action=(
                None
                if object_store_provider == "s3"
                else "The deploy path will override this to s3 for AWS."
            ),
        ),
    ]


def _build_production_posture_checks(
    *,
    env_values: dict[str, str],
    project_config: ProjectConfig | None,
) -> list[DiagnosticCheck]:
    backend_profile = _first_non_empty(
        env_values.get("BACKEND_PROFILE"),
        None if project_config is None else project_config.security.backend_profile,
    )
    return [
        DiagnosticCheck(
            id="production_backend_profile",
            status="pass" if backend_profile == "production" else "warn",
            message=(
                "BACKEND_PROFILE is production."
                if backend_profile == "production"
                else "BACKEND_PROFILE is not explicitly set to production."
            ),
            action=(
                None
                if backend_profile == "production"
                else "The deploy path will force production settings, but recording them in config is recommended."
            ),
        ),
    ]


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
