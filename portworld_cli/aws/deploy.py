from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import click

from portworld_cli.aws.common import aws_cli_available
from portworld_cli.aws.constants import COMMAND_NAME
from portworld_cli.aws.stages import (
    DeployAWSECSFargateOptions,
    build_and_push_image,
    build_runtime_env_vars,
    docker_login_to_ecr,
    ensure_alb_listener,
    ensure_application_load_balancer,
    ensure_cloudfront_distribution,
    ensure_ecs_cluster,
    ensure_ecs_execution_role,
    ensure_ecs_log_group,
    ensure_ecs_service_linked_role,
    ensure_ecs_task_role,
    ensure_ecr_repository,
    ensure_s3_bucket,
    ensure_service_security_groups,
    ensure_target_group,
    register_task_definition,
    resolve_aws_deploy_config,
    resolve_or_provision_database,
    resolve_vpc_and_subnets,
    upsert_ecs_service,
    wait_for_cloudfront_deployed,
    wait_for_ecs_service_stable,
    wait_for_public_validation,
)
from portworld_cli.aws.stages.config import ResolvedAWSDeployConfig
from portworld_cli.aws.stages.shared import now_ms, normalize_service_url, stage_ok
from portworld_cli.context import CLIContext
from portworld_cli.deploy.config import DeployStageError, DeployUsageError, load_deploy_session
from portworld_cli.deploy.reporting import humanize_stage_label
from portworld_cli.deploy_artifacts import IMAGE_SOURCE_MODE_SOURCE_BUILD
from portworld_cli.deploy_state import DeployState, write_deploy_state
from portworld_cli.output import CommandResult
from portworld_cli.targets import TARGET_AWS_ECS_FARGATE
from portworld_cli.ux.prompts import prompt_confirm
from portworld_cli.ux.progress import ProgressReporter


@dataclass(frozen=True, slots=True)
class AWSDeployMutationResult:
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
    progress = ProgressReporter(cli_context)
    try:
        with progress.stage(humanize_stage_label("repo_config_discovery")):
            session = load_deploy_session(cli_context)
            stage_records.append(stage_ok("repo_config_discovery", "Resolved workspace and loaded CLI config inputs."))

        with progress.stage(humanize_stage_label("prerequisite_validation")):
            if not aws_cli_available():
                raise DeployStageError(
                    stage="prerequisite_validation",
                    message="aws CLI is not installed or not on PATH.",
                    action="Install AWS CLI v2 and re-run deploy.",
                )
            stage_records.append(stage_ok("prerequisite_validation", "Validated aws CLI availability."))

        env_values = OrderedDict(session.merged_env_values().items())
        with progress.stage(humanize_stage_label("parameter_resolution")):
            config = resolve_aws_deploy_config(
                cli_context,
                options=options,
                env_values=env_values,
                project_config=session.project_config,
                runtime_source=session.effective_runtime_source,
                project_root=(None if session.project_paths is None else session.project_paths.project_root),
            )
            stage_records.append(stage_ok("parameter_resolution", "Resolved deploy parameters."))

        _confirm_mutations(cli_context, config)
        stage_records.append(stage_ok("mutation_plan", "Confirmed deploy mutations."))

        resources.update(
            {
                "account_id": config.account_id,
                "region": config.region,
                "ecs_service_name": config.app_name,
                "bucket_name": config.bucket_name,
                "image_uri": config.image_uri,
                "rds_instance_identifier": config.rds_instance_identifier,
            }
        )
        if config.ecr_repository is not None:
            resources["ecr_repository"] = config.ecr_repository

        result = _run_aws_deploy_mutations(
            config,
            env_values=env_values,
            stage_records=stage_records,
            project_root=session.workspace_root,
            progress=progress,
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

        with progress.stage(humanize_stage_label("post_deploy_validation")):
            livez_ok, ws_ok = wait_for_public_validation(
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
                stage_records.append(stage_ok("post_deploy_validation", "Validated /livez and /ws/session endpoint reachability."))

        with progress.stage(humanize_stage_label("state_write")):
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
                        last_deployed_at_ms=now_ms(),
                    ),
                )
            except Exception as exc:
                raise DeployStageError(
                    stage="state_write",
                    message=f"Unable to write AWS deploy state: {exc}",
                    action="Check workspace permissions for `.portworld/state` and retry.",
                ) from exc
            stage_records.append(stage_ok("state_write", "Wrote AWS deploy state."))

        runtime_env = build_runtime_env_vars(env_values, config, database_url=result.database_url)
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
            message=_problem_next_message(
                problem=str(exc),
                next_step=f"Run `{COMMAND_NAME} --help` and provide the required target inputs.",
                stage="parameter_resolution",
            ),
            data={
                "stage": "parameter_resolution",
                "error_type": type(exc).__name__,
                "resources": resources,
                "stages": stage_records,
            },
            exit_code=2,
        )
    except DeployStageError as exc:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message=_problem_next_message(
                problem=str(exc),
                next_step=exc.action or "Inspect the reported stage and rerun deploy.",
                stage=exc.stage,
            ),
            data={
                "stage": exc.stage,
                "error_type": type(exc).__name__,
                "resources": resources,
                "stages": stage_records,
            },
            exit_code=1,
        )
    except click.Abort:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message=_problem_next_message(
                problem="Deploy canceled before completion.",
                next_step=f"Rerun `{COMMAND_NAME}` when you are ready.",
                stage="mutation_plan",
            ),
            data={
                "stage": "mutation_plan",
                "error_type": "Abort",
                "resources": resources,
                "stages": stage_records,
            },
            exit_code=1,
        )
    finally:
        progress.close()


def _run_aws_deploy_mutations(
    config: ResolvedAWSDeployConfig,
    *,
    env_values: OrderedDict[str, str],
    stage_records: list[dict[str, object]],
    project_root: Path,
    progress: ProgressReporter,
) -> AWSDeployMutationResult:
    with progress.stage(humanize_stage_label("aws_artifact_setup")):
        ensure_s3_bucket(config, stage_records=stage_records)

    with progress.stage(humanize_stage_label("aws_image_publish")):
        if config.image_source_mode == IMAGE_SOURCE_MODE_SOURCE_BUILD:
            ensure_ecr_repository(config, stage_records=stage_records)
            docker_login_to_ecr(config, stage_records=stage_records)
            build_and_push_image(config, stage_records=stage_records, project_root=project_root)
        else:
            stage_records.append(
                stage_ok(
                    "publish_image",
                    f"Using published image `{config.image_uri}`.",
                )
            )

    with progress.stage(humanize_stage_label("aws_database_setup")):
        database_resolution = resolve_or_provision_database(config, stage_records=stage_records)

    with progress.stage(humanize_stage_label("aws_network_edge_setup")):
        vpc_id, subnet_ids = resolve_vpc_and_subnets(config)
        alb_security_group_id, ecs_security_group_id = ensure_service_security_groups(
            config=config,
            vpc_id=vpc_id,
            rds_security_group_id=database_resolution.rds_security_group_id,
            stage_records=stage_records,
        )
        alb_arn, alb_dns_name = ensure_application_load_balancer(
            config=config,
            subnet_ids=subnet_ids,
            alb_security_group_id=alb_security_group_id,
            stage_records=stage_records,
        )
        target_group_arn = ensure_target_group(
            config=config,
            vpc_id=vpc_id,
            stage_records=stage_records,
        )
        ensure_alb_listener(
            config=config,
            alb_arn=alb_arn,
            target_group_arn=target_group_arn,
            stage_records=stage_records,
        )
        cloudfront_distribution_id, cloudfront_domain_name = ensure_cloudfront_distribution(
            config=config,
            alb_dns_name=alb_dns_name,
            stage_records=stage_records,
        )
    runtime_env = build_runtime_env_vars(
        env_values,
        config,
        database_url=database_resolution.database_url,
    )
    with progress.stage(humanize_stage_label("aws_runtime_setup")):
        execution_role_arn = ensure_ecs_execution_role(stage_records=stage_records)
        task_role_arn = ensure_ecs_task_role(config=config, stage_records=stage_records)
        log_group_name = ensure_ecs_log_group(config=config, stage_records=stage_records)
        cluster_name = ensure_ecs_cluster(config=config, stage_records=stage_records)
        ensure_ecs_service_linked_role(stage_records=stage_records)
        task_definition_arn = register_task_definition(
            config=config,
            runtime_env=runtime_env,
            execution_role_arn=execution_role_arn,
            task_role_arn=task_role_arn,
            log_group_name=log_group_name,
            stage_records=stage_records,
        )
        service_name = upsert_ecs_service(
            config=config,
            cluster_name=cluster_name,
            task_definition_arn=task_definition_arn,
            subnet_ids=subnet_ids,
            ecs_security_group_id=ecs_security_group_id,
            target_group_arn=target_group_arn,
            stage_records=stage_records,
        )
    with progress.stage(humanize_stage_label("aws_rollout_wait")):
        wait_for_ecs_service_stable(
            config=config,
            cluster_name=cluster_name,
            service_name=service_name,
            expected_task_definition_arn=task_definition_arn,
            stage_records=stage_records,
        )
        wait_for_cloudfront_deployed(
            distribution_id=cloudfront_distribution_id,
            stage_records=stage_records,
        )
    service_url = normalize_service_url(cloudfront_domain_name)
    return AWSDeployMutationResult(
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


def _confirm_mutations(cli_context: CLIContext, config: ResolvedAWSDeployConfig) -> None:
    if cli_context.non_interactive or cli_context.yes:
        return
    confirmed = prompt_confirm(
        cli_context,
        message="\n".join(
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
    )
    if not confirmed:
        raise click.Abort()


def _problem_next_message(*, problem: str, next_step: str, stage: str | None = None) -> str:
    lines: list[str] = []
    if stage:
        lines.append(f"stage: {stage}")
    lines.append(f"problem: {problem}")
    lines.append(f"next: {next_step}")
    return "\n".join(lines)
