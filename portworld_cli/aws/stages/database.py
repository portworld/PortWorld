from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from portworld_cli.aws.common import run_aws_json, run_aws_text
from portworld_cli.aws.constants import RDS_INSTANCE_CLASS, RDS_STORAGE_GB
from portworld_cli.aws.stages.config import ResolvedAWSDeployConfig
from portworld_cli.aws.stages.shared import generate_rds_password, read_dict_string, stage_ok, to_json_argument
from portworld_cli.deploy.config import DeployStageError


@dataclass(frozen=True, slots=True)
class DatabaseResolution:
    database_url: str
    resolved_vpc_id: str | None
    resolved_subnet_ids: tuple[str, ...]
    rds_security_group_id: str | None
    used_external_database: bool


def resolve_or_provision_database(
    config: ResolvedAWSDeployConfig,
    *,
    stage_records: list[dict[str, object]],
) -> DatabaseResolution:
    if config.explicit_database_url:
        stage_records.append(stage_ok("rds_database", "Using externally provided BACKEND_DATABASE_URL."))
        return DatabaseResolution(
            database_url=config.explicit_database_url,
            resolved_vpc_id=None,
            resolved_subnet_ids=(),
            rds_security_group_id=None,
            used_external_database=True,
        )

    vpc_id, subnet_ids = resolve_vpc_and_subnets(config)
    subnet_group_name = f"{config.rds_instance_identifier}-subnets"
    ensure_db_subnet_group(
        region=config.region,
        subnet_group_name=subnet_group_name,
        subnet_ids=subnet_ids,
        stage_records=stage_records,
    )
    security_group_id = ensure_rds_security_group(
        region=config.region,
        vpc_id=vpc_id,
        app_name=config.app_name,
        stage_records=stage_records,
    )
    database_url = ensure_rds_instance(
        config,
        subnet_group_name=subnet_group_name,
        security_group_id=security_group_id,
        stage_records=stage_records,
    )
    return DatabaseResolution(
        database_url=database_url,
        resolved_vpc_id=vpc_id,
        resolved_subnet_ids=subnet_ids,
        rds_security_group_id=security_group_id,
        used_external_database=False,
    )


def resolve_vpc_and_subnets(config: ResolvedAWSDeployConfig) -> tuple[str, tuple[str, ...]]:
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
        vpc_id = read_dict_string(vpcs[0], "VpcId")
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
        subnet_ids = select_subnets_for_rds(subnet_result.value)

    if len(subnet_ids) < 2:
        raise DeployStageError(
            stage="rds_network",
            message="RDS requires at least two subnets in distinct availability zones.",
            action="Provide --subnet-ids with at least two subnets across different AZs.",
        )
    return vpc_id, subnet_ids


def select_subnets_for_rds(payload: dict[str, object]) -> tuple[str, ...]:
    subnets = payload.get("Subnets")
    if not isinstance(subnets, list):
        return ()
    selected: list[str] = []
    seen_az: set[str] = set()
    sortable: list[tuple[str, str]] = []
    for subnet in subnets:
        if not isinstance(subnet, dict):
            continue
        subnet_id = read_dict_string(subnet, "SubnetId")
        az = read_dict_string(subnet, "AvailabilityZone")
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


def ensure_db_subnet_group(
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
        stage_records.append(stage_ok("rds_subnet_group", f"RDS subnet group `{subnet_group_name}` is ready."))
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
    stage_records.append(stage_ok("rds_subnet_group", f"Created RDS subnet group `{subnet_group_name}`."))


def ensure_rds_security_group(
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
        existing_group_id = read_dict_string(groups[0], "GroupId")
        if existing_group_id:
            ensure_rds_security_group_ingress(region=region, group_id=existing_group_id)
            stage_records.append(stage_ok("rds_security_group", f"RDS security group `{existing_group_id}` is ready."))
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
    group_id = read_dict_string(created.value, "GroupId")
    if not group_id:
        raise DeployStageError(
            stage="rds_security_group",
            message="Unable to read created RDS security group id.",
            action="Retry deploy.",
        )
    ensure_rds_security_group_ingress(region=region, group_id=group_id)
    stage_records.append(stage_ok("rds_security_group", f"Created RDS security group `{group_id}`."))
    return group_id


def ensure_rds_security_group_ingress(*, region: str, group_id: str) -> None:
    ingress = run_aws_json(
        [
            "ec2",
            "authorize-security-group-ingress",
            "--region",
            region,
            "--group-id",
            group_id,
            "--ip-permissions",
            to_json_argument(
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


def ensure_rds_instance(
    config: ResolvedAWSDeployConfig,
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
        password = resolve_rds_password(config)
        endpoint = wait_for_rds_endpoint(
            region=config.region,
            db_instance_identifier=config.rds_instance_identifier,
            stage_records=stage_records,
        )
        stage_records.append(stage_ok("rds_instance", f"RDS instance `{config.rds_instance_identifier}` is ready."))
        return build_postgres_url(
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

    password = generate_rds_password()
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
    store_rds_password(config, password)
    endpoint = wait_for_rds_endpoint(
        region=config.region,
        db_instance_identifier=config.rds_instance_identifier,
        stage_records=stage_records,
    )
    stage_records.append(stage_ok("rds_instance", f"Created RDS instance `{config.rds_instance_identifier}`."))
    return build_postgres_url(
        username=config.rds_master_username,
        password=password,
        host=endpoint[0],
        port=endpoint[1],
        db_name=config.rds_db_name,
    )


def wait_for_rds_endpoint(
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
    stage_records.append(stage_ok("rds_instance_wait_available", f"RDS instance `{db_instance_identifier}` is available."))

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
    endpoint = extract_rds_endpoint(described.value)
    if endpoint is None:
        raise DeployStageError(
            stage="rds_instance_wait_available",
            message="RDS endpoint address/port is not available.",
            action="Retry deploy after RDS instance fully initializes.",
        )
    return endpoint


def extract_rds_endpoint(payload: dict[str, object]) -> tuple[str, int] | None:
    instances = payload.get("DBInstances")
    if not isinstance(instances, list) or len(instances) == 0 or not isinstance(instances[0], dict):
        return None
    endpoint = instances[0].get("Endpoint")
    if not isinstance(endpoint, dict):
        return None
    address = read_dict_string(endpoint, "Address")
    port = endpoint.get("Port")
    if address is None or not isinstance(port, int):
        return None
    return (address, port)


def store_rds_password(config: ResolvedAWSDeployConfig, password: str) -> None:
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


def resolve_rds_password(config: ResolvedAWSDeployConfig) -> str:
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
    value = read_dict_string(parameter, "Value")
    if not value:
        raise DeployStageError(
            stage="rds_password_read",
            message="RDS password parameter was empty.",
            action="Pass --database-url explicitly or rewrite password parameter.",
        )
    return value


def build_postgres_url(*, username: str, password: str, host: str, port: int, db_name: str) -> str:
    return f"postgresql://{quote(username)}:{quote(password)}@{host}:{port}/{db_name}"
