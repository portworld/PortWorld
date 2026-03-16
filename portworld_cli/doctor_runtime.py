from __future__ import annotations

from dataclasses import dataclass

from portworld_cli.config_runtime import (
    ConfigRuntimeError,
    load_config_session,
)
from portworld_cli.context import CLIContext
from portworld_cli.envfile import EnvFileParseError
from portworld_cli.gcp.doctor import evaluate_gcp_cloud_run_readiness
from portworld_cli.output import CommandResult, DiagnosticCheck, format_key_value_lines
from portworld_cli.paths import ProjectRootResolutionError
from portworld_cli.project_config import ProjectConfigError
from portworld_cli.runtime.published import run_local_doctor_published
from portworld_cli.runtime.reporting import (
    ExternalCommandResult as _ExternalCommandResult,
    probe_external_command as _probe_command,
)
from portworld_cli.runtime.source import run_local_doctor_source
from portworld_cli.state import CLIStateDecodeError, CLIStateTypeError


COMMAND_NAME = "portworld doctor"


@dataclass(frozen=True, slots=True)
class DoctorOptions:
    target: str
    full: bool
    project: str | None
    region: str | None


def run_doctor(cli_context: CLIContext, options: DoctorOptions) -> CommandResult:
    if options.target == "gcp-cloud-run":
        return _run_gcp_cloud_run_doctor(cli_context, options=options)
    if options.project is not None or options.region is not None:
        return _usage_error_result(
            "--project and --region are only supported with --target gcp-cloud-run."
        )
    return _run_local_doctor(cli_context, full=options.full)


def _run_local_doctor(cli_context: CLIContext, *, full: bool) -> CommandResult:
    try:
        config_session = load_config_session(cli_context)
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
        session = load_config_session(cli_context)
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


def _usage_error_result(message: str) -> CommandResult:
    return CommandResult(
        ok=False,
        command=COMMAND_NAME,
        message=message,
        data={
            "status": "error",
            "error_type": "UsageError",
        },
        exit_code=2,
    )
