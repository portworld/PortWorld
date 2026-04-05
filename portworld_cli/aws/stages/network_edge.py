from __future__ import annotations

from time import monotonic
import time

from portworld_cli.aws.common import run_aws_json
from portworld_cli.aws.constants import MANAGED_CACHE_POLICY_CACHING_DISABLED, MANAGED_ORIGIN_REQUEST_POLICY_ALL_VIEWER
from portworld_cli.aws.stages.config import ResolvedAWSDeployConfig
from portworld_cli.aws.stages.shared import now_ms, read_dict_string, stage_ok, to_json_argument
from portworld_cli.deploy.config import DeployStageError


def ensure_service_security_groups(
    *,
    config: ResolvedAWSDeployConfig,
    vpc_id: str,
    rds_security_group_id: str | None,
    stage_records: list[dict[str, object]],
) -> tuple[str, str]:
    alb_group_name = f"{config.app_name}-alb-sg"
    ecs_group_name = f"{config.app_name}-ecs-sg"
    alb_sg = ensure_security_group(
        region=config.region,
        vpc_id=vpc_id,
        group_name=alb_group_name,
        description="PortWorld ALB ingress",
    )
    ecs_sg = ensure_security_group(
        region=config.region,
        vpc_id=vpc_id,
        group_name=ecs_group_name,
        description="PortWorld ECS ingress",
    )
    authorize_ingress_cidr(region=config.region, group_id=alb_sg, port=80, cidr="0.0.0.0/0")
    authorize_ingress_sg(region=config.region, group_id=ecs_sg, port=8080, source_group_id=alb_sg)
    if rds_security_group_id:
        authorize_ingress_sg(region=config.region, group_id=rds_security_group_id, port=5432, source_group_id=ecs_sg)
    stage_records.append(stage_ok("service_security_groups", f"Configured ALB `{alb_sg}` and ECS `{ecs_sg}` security groups."))
    return alb_sg, ecs_sg


def ensure_security_group(*, region: str, vpc_id: str, group_name: str, description: str) -> str:
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
        group_id = read_dict_string(groups[0], "GroupId")
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
    group_id = read_dict_string(created.value, "GroupId")
    if not group_id:
        raise DeployStageError(
            stage="service_security_groups",
            message=f"Unable to resolve security group id for `{group_name}`.",
            action="Retry deploy.",
        )
    return group_id


def authorize_ingress_cidr(*, region: str, group_id: str, port: int, cidr: str) -> None:
    result = run_aws_json(
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


def authorize_ingress_sg(*, region: str, group_id: str, port: int, source_group_id: str) -> None:
    result = run_aws_json(
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


def ensure_application_load_balancer(
    *,
    config: ResolvedAWSDeployConfig,
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
            alb_arn = read_dict_string(lbs[0], "LoadBalancerArn")
            dns_name = read_dict_string(lbs[0], "DNSName")
            if alb_arn and dns_name:
                stage_records.append(stage_ok("alb", f"ALB `{alb_name}` is ready."))
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
    alb_arn = read_dict_string(lbs[0], "LoadBalancerArn")
    dns_name = read_dict_string(lbs[0], "DNSName")
    if not alb_arn or not dns_name:
        raise DeployStageError(
            stage="alb",
            message="ALB create response missing ARN or DNS name.",
            action="Retry deploy.",
        )
    stage_records.append(stage_ok("alb", f"Created ALB `{alb_name}`."))
    return alb_arn, dns_name


def ensure_target_group(
    *,
    config: ResolvedAWSDeployConfig,
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
            tg_arn = read_dict_string(groups[0], "TargetGroupArn")
            if tg_arn:
                stage_records.append(stage_ok("target_group", f"Target group `{tg_name}` is ready."))
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
    tg_arn = read_dict_string(groups[0], "TargetGroupArn")
    if not tg_arn:
        raise DeployStageError(
            stage="target_group",
            message="Target group create response missing ARN.",
            action="Retry deploy.",
        )
    stage_records.append(stage_ok("target_group", f"Created target group `{tg_name}`."))
    return tg_arn


def ensure_alb_listener(
    *,
    config: ResolvedAWSDeployConfig,
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
                arn = read_dict_string(listener, "ListenerArn")
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
                            to_json_argument(
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
                    stage_records.append(stage_ok("alb_listener", "ALB listener on port 80 is ready."))
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
            to_json_argument(
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
    stage_records.append(stage_ok("alb_listener", "Created ALB listener on port 80."))


def ensure_cloudfront_distribution(
    *,
    config: ResolvedAWSDeployConfig,
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
                if read_dict_string(item, "Comment") != comment:
                    continue
                dist_id = read_dict_string(item, "Id")
                domain_name = read_dict_string(item, "DomainName")
                if dist_id and domain_name:
                    stage_records.append(stage_ok("cloudfront", f"CloudFront distribution `{dist_id}` is ready."))
                    return dist_id, domain_name

    caller_reference = f"{config.app_name}-{now_ms()}"
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
            to_json_argument(distribution_config),
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
    dist_id = read_dict_string(dist, "Id")
    domain_name = read_dict_string(dist, "DomainName")
    if not dist_id or not domain_name:
        raise DeployStageError(
            stage="cloudfront",
            message="CloudFront create response missing Id or DomainName.",
            action="Retry deploy.",
        )
    stage_records.append(stage_ok("cloudfront", f"Created CloudFront distribution `{dist_id}`."))
    return dist_id, domain_name


def wait_for_cloudfront_deployed(
    *,
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
        status = read_dict_string(dist, "Status") or "UNKNOWN"
        if status.upper() == "DEPLOYED":
            stage_records.append(stage_ok("cloudfront_wait_deployed", f"CloudFront distribution `{distribution_id}` is deployed."))
            return
        time.sleep(15)
    raise DeployStageError(
        stage="cloudfront_wait_deployed",
        message=f"Timed out waiting for CloudFront distribution `{distribution_id}` to deploy.",
        action="Inspect CloudFront deployment status and retry.",
    )
