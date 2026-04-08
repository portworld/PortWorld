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
from portworld_cli.services.cloud_contract import (
    AWSDoctorOptions,
    AzureDoctorOptions,
    CloudProviderOptions,
    GCPDoctorOptions,
    problem_next_message,
    to_aws_doctor_options,
    to_azure_doctor_options,
    to_gcp_doctor_options,
    validate_cloud_flag_scope_for_doctor,
)
from portworld_cli.services.config.errors import ConfigRuntimeError
from portworld_cli.targets import TARGET_AWS_ECS_FARGATE, normalize_managed_target
from portworld_cli.ux.progress import ProgressReporter
from portworld_cli.workspace.discovery.paths import ProjectRootResolutionError
from portworld_cli.workspace.state.state_store import CLIStateDecodeError, CLIStateTypeError
from portworld_cli.workspace.session import load_workspace_session


COMMAND_NAME = "portworld doctor"


@dataclass(frozen=True, slots=True)
class DoctorOptions:
    target: str
    full: bool
    cloud: CloudProviderOptions


def run_doctor(cli_context: CLIContext, options: DoctorOptions) -> CommandResult:
    progress = ProgressReporter(cli_context)
    progress.start("Verifying setup and deployment readiness")
    normalized_target = normalize_managed_target(options.target) or options.target
    try:
        issue = validate_cloud_flag_scope_for_doctor(
            target=options.target,
            cloud_options=options.cloud,
        )
        if issue is not None:
            result = _usage_error_result(
                problem=issue.problem,
                next_step=issue.next_step,
                target=options.target,
            )
        elif options.target == "gcp-cloud-run":
            result = _run_gcp_cloud_run_doctor(cli_context, options=options)
        elif normalized_target == TARGET_AWS_ECS_FARGATE:
            result = _run_aws_ecs_fargate_doctor(cli_context, options=options)
        elif options.target == "azure-container-apps":
            result = _run_azure_container_apps_doctor(cli_context, options=options)
        else:
            result = _run_local_doctor(cli_context, full=options.full)
        if result.ok:
            progress.complete()
        else:
            progress.fail()
        return _finalize_doctor_result(cli_context, result)
    finally:
        progress.close()


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
    gcp_options: GCPDoctorOptions = to_gcp_doctor_options(options.cloud)
    try:
        session = load_workspace_session(cli_context)
    except ProjectRootResolutionError as exc:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message=format_key_value_lines(
                ("target", options.target),
                ("full", options.full),
                ("gcp_project", gcp_options.project),
                ("gcp_region", gcp_options.region),
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
        explicit_project=gcp_options.project,
        explicit_region=gcp_options.region,
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
            ("gcp_project", details.project_id or gcp_options.project),
            ("gcp_region", details.region or gcp_options.region),
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
    aws_options: AWSDoctorOptions = to_aws_doctor_options(options.cloud)
    try:
        session = load_workspace_session(cli_context)
    except ProjectRootResolutionError as exc:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message=format_key_value_lines(
                ("target", options.target),
                ("full", options.full),
                ("aws_region", aws_options.region),
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
        explicit_region=aws_options.region,
        explicit_service=aws_options.service,
        explicit_vpc_id=aws_options.vpc_id,
        explicit_subnet_ids=aws_options.subnet_ids,
        explicit_s3_bucket=aws_options.s3_bucket,
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
    azure_options: AzureDoctorOptions = to_azure_doctor_options(options.cloud)
    try:
        session = load_workspace_session(cli_context)
    except ProjectRootResolutionError as exc:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message=format_key_value_lines(
                ("target", options.target),
                ("full", options.full),
                ("azure_subscription", azure_options.subscription),
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
        explicit_subscription=azure_options.subscription,
        explicit_resource_group=azure_options.resource_group,
        explicit_region=azure_options.region,
        explicit_environment=azure_options.environment,
        explicit_app=azure_options.app,
        explicit_storage_account=azure_options.storage_account,
        explicit_blob_container=azure_options.blob_container,
        explicit_blob_endpoint=azure_options.blob_endpoint,
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


def _usage_error_result(*, problem: str, next_step: str, target: str) -> CommandResult:
    return CommandResult(
        ok=False,
        command=COMMAND_NAME,
        message=problem_next_message(problem=problem, next_step=next_step),
        data={
            "status": "error",
            "error_type": "UsageError",
            "target": target,
        },
        exit_code=2,
    )


def _finalize_doctor_result(cli_context: CLIContext, result: CommandResult) -> CommandResult:
    if cli_context.json_output:
        return result
    if result.data.get("error_type") is not None:
        return result

    all_checks = tuple(result.checks)
    filtered_checks = _doctor_non_pass_checks(all_checks)
    if filtered_checks:
        return CommandResult(
            ok=result.ok,
            command=result.command,
            message=None,
            data={
                **result.data,
                "all_checks": [check.to_dict() for check in all_checks],
            },
            checks=filtered_checks,
            exit_code=result.exit_code,
        )

    target = str(result.data.get("target") or "local")
    return CommandResult(
        ok=result.ok,
        command=result.command,
        message=f"ready: target {target}",
        data={
            **result.data,
            "all_checks": [check.to_dict() for check in all_checks],
        },
        checks=(),
        exit_code=result.exit_code,
    )


def _doctor_non_pass_checks(checks: tuple[DiagnosticCheck, ...]) -> tuple[DiagnosticCheck, ...]:
    return tuple(check for check in checks if check.status != "pass")
