from __future__ import annotations

from dataclasses import dataclass

from portworld_cli.aws.doctor import evaluate_aws_ecs_fargate_readiness
from portworld_cli.azure.doctor import evaluate_azure_container_apps_readiness
from portworld_cli.context import CLIContext
from portworld_cli.envfile import EnvFileParseError
from portworld_cli.gcp.doctor import evaluate_gcp_cloud_run_readiness
from portworld_cli.output import CommandResult, DiagnosticCheck, format_key_value_lines
from portworld_cli.workspace.project_config import ProjectConfigError
from portworld_cli.runtime.published import run_local_doctor_published
from portworld_cli.runtime.source import run_local_doctor_source
from portworld_cli.services.config.errors import ConfigRuntimeError
from portworld_cli.targets import TARGET_AWS_ECS_FARGATE, normalize_managed_target
from portworld_cli.workspace.discovery.paths import ProjectRootResolutionError
from portworld_cli.workspace.state.state_store import CLIStateDecodeError, CLIStateTypeError
from portworld_cli.workspace.session import load_workspace_session


COMMAND_NAME = "portworld doctor"


@dataclass(frozen=True, slots=True)
class DoctorOptions:
    target: str
    full: bool
    project: str | None
    region: str | None
    aws_region: str | None
    aws_service: str | None
    aws_vpc_id: str | None
    aws_subnet_ids: str | None
    aws_database_url: str | None
    aws_s3_bucket: str | None
    azure_subscription: str | None
    azure_resource_group: str | None
    azure_region: str | None
    azure_environment: str | None
    azure_app: str | None
    azure_database_url: str | None
    azure_storage_account: str | None
    azure_blob_container: str | None
    azure_blob_endpoint: str | None


def run_doctor(cli_context: CLIContext, options: DoctorOptions) -> CommandResult:
    normalized_target = normalize_managed_target(options.target) or options.target
    if options.target == "gcp-cloud-run":
        if (
            options.aws_region is not None
            or options.aws_service is not None
            or options.aws_vpc_id is not None
            or options.aws_subnet_ids is not None
            or options.aws_database_url is not None
            or options.aws_s3_bucket is not None
            or options.azure_subscription is not None
            or options.azure_resource_group is not None
            or options.azure_region is not None
            or options.azure_environment is not None
            or options.azure_app is not None
            or options.azure_database_url is not None
            or options.azure_storage_account is not None
            or options.azure_blob_container is not None
            or options.azure_blob_endpoint is not None
        ):
            return _usage_error_result(
                problem="AWS/Azure flags are only supported with their matching cloud targets.",
                next_step="Use only GCP flags with `--target gcp-cloud-run`, or switch `--target` to the matching cloud.",
            )
        return _run_gcp_cloud_run_doctor(cli_context, options=options)
    if normalized_target == TARGET_AWS_ECS_FARGATE:
        if (
            options.project is not None
            or options.region is not None
            or options.azure_subscription is not None
            or options.azure_resource_group is not None
            or options.azure_region is not None
            or options.azure_environment is not None
            or options.azure_app is not None
            or options.azure_database_url is not None
            or options.azure_storage_account is not None
            or options.azure_blob_container is not None
            or options.azure_blob_endpoint is not None
        ):
            return _usage_error_result(
                problem="GCP/Azure flags are not supported with --target aws-ecs-fargate.",
                next_step="Use only AWS flags with `--target aws-ecs-fargate`, or switch `--target`.",
            )
        return _run_aws_ecs_fargate_doctor(cli_context, options=options)
    if options.target == "azure-container-apps":
        if (
            options.project is not None
            or options.region is not None
            or options.aws_region is not None
            or options.aws_service is not None
            or options.aws_vpc_id is not None
            or options.aws_subnet_ids is not None
            or options.aws_database_url is not None
            or options.aws_s3_bucket is not None
        ):
            return _usage_error_result(
                problem="GCP/AWS flags are not supported with --target azure-container-apps.",
                next_step="Use only Azure flags with `--target azure-container-apps`, or switch `--target`.",
            )
        return _run_azure_container_apps_doctor(cli_context, options=options)
    if (
        options.project is not None
        or options.region is not None
        or options.aws_region is not None
        or options.aws_service is not None
        or options.aws_vpc_id is not None
        or options.aws_subnet_ids is not None
        or options.aws_database_url is not None
        or options.aws_s3_bucket is not None
        or options.azure_subscription is not None
        or options.azure_resource_group is not None
        or options.azure_region is not None
        or options.azure_environment is not None
        or options.azure_app is not None
        or options.azure_database_url is not None
        or options.azure_storage_account is not None
        or options.azure_blob_container is not None
        or options.azure_blob_endpoint is not None
    ):
        return _usage_error_result(
            problem=(
                "Cloud target options are only supported with --target gcp-cloud-run, "
                "--target aws-ecs-fargate, or --target azure-container-apps."
            ),
            next_step="Run `portworld doctor --target local` without cloud flags, or choose a managed target.",
        )
    return _run_local_doctor(cli_context, full=options.full)


def _run_local_doctor(cli_context: CLIContext, *, full: bool) -> CommandResult:
    try:
        config_session = load_workspace_session(cli_context)
    except ProjectRootResolutionError as exc:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message=format_key_value_lines(
                ("target", "local"),
                ("full", full),
            ),
            data={
                "target": "local",
                "project_root": None,
                "full": full,
                "status": "error",
                "error_type": type(exc).__name__,
            },
            checks=(
                DiagnosticCheck(
                    id="project_root_detected",
                    status="fail",
                    message=str(exc),
                    action="Run from a PortWorld repo checkout, a published workspace, or pass --project-root.",
                ),
            ),
            exit_code=1,
        )
    except (
        CLIStateDecodeError,
        CLIStateTypeError,
        EnvFileParseError,
        ProjectConfigError,
        ConfigRuntimeError,
    ) as exc:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message=str(exc),
            data={
                "target": "local",
                "project_root": None,
                "full": full,
                "status": "error",
                "error_type": type(exc).__name__,
            },
            exit_code=2,
        )

    if config_session.effective_runtime_source == "published":
        return _run_published_local_doctor(config_session, full=full)

    return run_local_doctor_source(
        config_session,
        full=full,
        command_name=COMMAND_NAME,
    )


def _run_published_local_doctor(config_session, *, full: bool) -> CommandResult:
    return run_local_doctor_published(
        config_session,
        full=full,
        command_name=COMMAND_NAME,
    )


def _run_gcp_cloud_run_doctor(
    cli_context: CLIContext,
    *,
    options: DoctorOptions,
) -> CommandResult:
    try:
        session = load_workspace_session(cli_context)
    except ProjectRootResolutionError as exc:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message=format_key_value_lines(
                ("target", options.target),
                ("full", options.full),
                ("project", options.project),
                ("region", options.region),
            ),
            data={
                "target": options.target,
                "project_root": None,
                "full": options.full,
                "status": "error",
                "error_type": type(exc).__name__,
            },
            checks=(
                DiagnosticCheck(
                    id="project_root_detected",
                    status="fail",
                    message=str(exc),
                    action="Run from a PortWorld repo checkout, a published workspace, or pass --project-root.",
                ),
            ),
            exit_code=1,
        )
    except (
        CLIStateDecodeError,
        CLIStateTypeError,
        EnvFileParseError,
        ProjectConfigError,
        ConfigRuntimeError,
    ) as exc:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message=str(exc),
            data={
                "target": options.target,
                "workspace_root": None,
                "project_root": None,
                "full": options.full,
                "status": "error",
                "error_type": type(exc).__name__,
            },
            exit_code=2,
        )

    evaluation = evaluate_gcp_cloud_run_readiness(
        source_project_paths=session.project_paths,
        full=options.full,
        explicit_project=options.project,
        explicit_region=options.region,
        project_config=session.project_config,
    )
    root_check = DiagnosticCheck(
        id="workspace_root_detected",
        status="pass",
        message=(
            f"PortWorld source workspace detected at {session.workspace_root}"
            if session.project_paths is not None
            else f"PortWorld published workspace detected at {session.workspace_root}"
        ),
    )
    checks = (root_check, *evaluation.checks)
    details = evaluation.details
    return CommandResult(
        ok=evaluation.ok,
        command=COMMAND_NAME,
        message=format_key_value_lines(
            ("target", options.target),
            ("full", options.full),
            ("workspace_root", session.workspace_root),
            ("workspace_resolution_source", session.workspace_resolution_source),
            ("active_workspace_root", session.active_workspace_root),
            (
                "project_root",
                None if session.project_paths is None else session.project_paths.project_root,
            ),
            ("project", details.project_id or options.project),
            ("region", details.region or options.region),
        ),
        data={
            "target": options.target,
            "workspace_root": str(session.workspace_root),
            "project_root": (
                None
                if session.project_paths is None
                else str(session.project_paths.project_root)
            ),
            "workspace_resolution_source": session.workspace_resolution_source,
            "active_workspace_root": (
                None if session.active_workspace_root is None else str(session.active_workspace_root)
            ),
            "full": options.full,
            "details": details.to_dict(),
        },
        checks=checks,
        exit_code=0 if evaluation.ok else 1,
    )


def _run_aws_ecs_fargate_doctor(
    cli_context: CLIContext,
    *,
    options: DoctorOptions,
) -> CommandResult:
    try:
        session = load_workspace_session(cli_context)
    except ProjectRootResolutionError as exc:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message=format_key_value_lines(
                ("target", options.target),
                ("full", options.full),
                ("aws_region", options.aws_region),
            ),
            data={
                "target": options.target,
                "project_root": None,
                "full": options.full,
                "status": "error",
                "error_type": type(exc).__name__,
            },
            checks=(
                DiagnosticCheck(
                    id="project_root_detected",
                    status="fail",
                    message=str(exc),
                    action="Run from a PortWorld repo checkout, a published workspace, or pass --project-root.",
                ),
            ),
            exit_code=1,
        )
    except (
        CLIStateDecodeError,
        CLIStateTypeError,
        EnvFileParseError,
        ProjectConfigError,
        ConfigRuntimeError,
    ) as exc:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message=str(exc),
            data={
                "target": options.target,
                "workspace_root": None,
                "project_root": None,
                "full": options.full,
                "status": "error",
                "error_type": type(exc).__name__,
            },
            exit_code=2,
        )

    root_check = DiagnosticCheck(
        id="workspace_root_detected",
        status="pass",
        message=(
            f"PortWorld source workspace detected at {session.workspace_root}"
            if session.project_paths is not None
            else f"PortWorld published workspace detected at {session.workspace_root}"
        ),
    )
    evaluation = evaluate_aws_ecs_fargate_readiness(
        runtime_source=session.effective_runtime_source,
        explicit_region=options.aws_region,
        explicit_service=options.aws_service,
        explicit_vpc_id=options.aws_vpc_id,
        explicit_subnet_ids=options.aws_subnet_ids,
        explicit_database_url=options.aws_database_url,
        explicit_s3_bucket=options.aws_s3_bucket,
        env_values=session.merged_env_values(),
        project_config=session.project_config,
    )
    checks = (root_check, *evaluation.checks)
    details = evaluation.details
    message_pairs: list[tuple[str, object | None]] = [
        ("target", options.target),
        ("full", options.full),
        ("workspace_root", session.workspace_root),
        ("workspace_resolution_source", session.workspace_resolution_source),
        ("active_workspace_root", session.active_workspace_root),
        (
            "project_root",
            None if session.project_paths is None else session.project_paths.project_root,
        ),
        ("aws_region", details.region),
        ("aws_ecs_cluster", details.cluster_name),
        ("aws_ecs_service", details.service_name),
        ("aws_vpc_id", details.vpc_id),
        ("aws_subnet_ids", ",".join(details.subnet_ids)),
        ("s3_bucket_name", details.bucket_name),
        ("aws_rds_instance", details.rds_instance_identifier),
        ("aws_alb_dns_name", details.alb_dns_name),
        ("aws_cloudfront_domain", details.cloudfront_domain_name),
        ("aws_service_url", details.service_url),
    ]
    if details.ecr_repository is not None:
        message_pairs.insert(12, ("aws_ecr_repository", details.ecr_repository))
    return CommandResult(
        ok=evaluation.ok,
        command=COMMAND_NAME,
        message=format_key_value_lines(*message_pairs),
        data={
            "target": options.target,
            "workspace_root": str(session.workspace_root),
            "project_root": (
                None
                if session.project_paths is None
                else str(session.project_paths.project_root)
            ),
            "workspace_resolution_source": session.workspace_resolution_source,
            "active_workspace_root": (
                None if session.active_workspace_root is None else str(session.active_workspace_root)
            ),
            "full": options.full,
            "details": details.to_dict(),
        },
        checks=checks,
        exit_code=0 if evaluation.ok else 1,
    )


def _run_azure_container_apps_doctor(
    cli_context: CLIContext,
    *,
    options: DoctorOptions,
) -> CommandResult:
    try:
        session = load_workspace_session(cli_context)
    except ProjectRootResolutionError as exc:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message=format_key_value_lines(
                ("target", options.target),
                ("full", options.full),
                ("azure_subscription", options.azure_subscription),
            ),
            data={
                "target": options.target,
                "project_root": None,
                "full": options.full,
                "status": "error",
                "error_type": type(exc).__name__,
            },
            checks=(
                DiagnosticCheck(
                    id="project_root_detected",
                    status="fail",
                    message=str(exc),
                    action="Run from a PortWorld repo checkout, a published workspace, or pass --project-root.",
                ),
            ),
            exit_code=1,
        )
    except (
        CLIStateDecodeError,
        CLIStateTypeError,
        EnvFileParseError,
        ProjectConfigError,
        ConfigRuntimeError,
    ) as exc:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message=str(exc),
            data={
                "target": options.target,
                "workspace_root": None,
                "project_root": None,
                "full": options.full,
                "status": "error",
                "error_type": type(exc).__name__,
            },
            exit_code=2,
        )

    root_check = DiagnosticCheck(
        id="workspace_root_detected",
        status="pass",
        message=(
            f"PortWorld source workspace detected at {session.workspace_root}"
            if session.project_paths is not None
            else f"PortWorld published workspace detected at {session.workspace_root}"
        ),
    )
    evaluation = evaluate_azure_container_apps_readiness(
        explicit_subscription=options.azure_subscription,
        explicit_resource_group=options.azure_resource_group,
        explicit_region=options.azure_region,
        explicit_environment=options.azure_environment,
        explicit_app=options.azure_app,
        explicit_database_url=options.azure_database_url,
        explicit_storage_account=options.azure_storage_account,
        explicit_blob_container=options.azure_blob_container,
        explicit_blob_endpoint=options.azure_blob_endpoint,
        env_values=session.merged_env_values(),
        project_config=session.project_config,
    )
    checks = (root_check, *evaluation.checks)
    details = evaluation.details
    return CommandResult(
        ok=evaluation.ok,
        command=COMMAND_NAME,
        message=format_key_value_lines(
            ("target", options.target),
            ("full", options.full),
            ("workspace_root", session.workspace_root),
            ("workspace_resolution_source", session.workspace_resolution_source),
            ("active_workspace_root", session.active_workspace_root),
            (
                "project_root",
                None if session.project_paths is None else session.project_paths.project_root,
            ),
            ("azure_subscription", details.subscription_id),
            ("azure_resource_group", details.resource_group),
            ("azure_region", details.region),
            ("azure_environment", details.environment_name),
            ("azure_app", details.app_name),
            ("azure_fqdn", details.fqdn),
            ("azure_storage_account", details.storage_account),
            ("azure_blob_container", details.blob_container),
            ("azure_blob_endpoint", details.blob_endpoint),
        ),
        data={
            "target": options.target,
            "workspace_root": str(session.workspace_root),
            "project_root": (
                None
                if session.project_paths is None
                else str(session.project_paths.project_root)
            ),
            "workspace_resolution_source": session.workspace_resolution_source,
            "active_workspace_root": (
                None if session.active_workspace_root is None else str(session.active_workspace_root)
            ),
            "full": options.full,
            "details": details.to_dict(),
        },
        checks=checks,
        exit_code=0 if evaluation.ok else 1,
    )


def _usage_error_result(*, problem: str, next_step: str) -> CommandResult:
    return CommandResult(
        ok=False,
        command=COMMAND_NAME,
        message=_problem_next_message(problem=problem, next_step=next_step),
        data={
            "status": "error",
            "error_type": "UsageError",
        },
        exit_code=2,
    )


def _problem_next_message(*, problem: str, next_step: str, stage: str | None = None) -> str:
    lines: list[str] = []
    if stage:
        lines.append(f"stage: {stage}")
    lines.append(f"problem: {problem}")
    lines.append(f"next: {next_step}")
    return "\n".join(lines)
