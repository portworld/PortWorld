from __future__ import annotations

from dataclasses import dataclass

import click

from portworld_cli.envfile import EnvFileParseError
from portworld_cli.output import CommandResult, DiagnosticCheck
from portworld_cli.workspace.project_config import ProjectConfigError
from portworld_cli.services.config.errors import ConfigRuntimeError
from portworld_cli.workspace.paths import ProjectRootResolutionError
from portworld_cli.workspace.state_store import CLIStateDecodeError, CLIStateTypeError

DEFAULT_PROJECT_ROOT_ACTION = (
    "Run from a PortWorld repo checkout, a published workspace, or pass --project-root."
)


@dataclass(frozen=True, slots=True)
class ErrorMappingPolicy:
    command_name: str
    project_root_exit_code: int = 1
    project_root_check_id: str | None = None
    project_root_action: str | None = None
    abort_message: str | None = None


_COMMON_EXIT_CODE_2_EXCEPTIONS: tuple[type[BaseException], ...] = (
    CLIStateDecodeError,
    CLIStateTypeError,
    EnvFileParseError,
    ProjectConfigError,
    ConfigRuntimeError,
)


def map_command_exception(
    exc: Exception,
    *,
    policy: ErrorMappingPolicy,
    usage_error_types: tuple[type[BaseException], ...] = (),
    exit_code_2_types: tuple[type[BaseException], ...] = (),
    include_common_exit_code_2: bool = True,
) -> CommandResult:
    if isinstance(exc, ProjectRootResolutionError):
        return _project_root_result(exc, policy=policy)
    if isinstance(exc, click.Abort):
        return _abort_result(policy=policy)

    exit_code_2_classes: tuple[type[BaseException], ...]
    if include_common_exit_code_2:
        exit_code_2_classes = _COMMON_EXIT_CODE_2_EXCEPTIONS + usage_error_types + exit_code_2_types
    else:
        exit_code_2_classes = usage_error_types + exit_code_2_types

    if isinstance(exc, exit_code_2_classes):
        return _failure_result(policy.command_name, exc, exit_code=2)
    return _failure_result(policy.command_name, exc, exit_code=1)


def _project_root_result(
    exc: ProjectRootResolutionError,
    *,
    policy: ErrorMappingPolicy,
) -> CommandResult:
    checks: tuple[DiagnosticCheck, ...] = ()
    if policy.project_root_check_id is not None:
        checks = (
            DiagnosticCheck(
                id=policy.project_root_check_id,
                status="fail",
                message=str(exc),
                action=policy.project_root_action or DEFAULT_PROJECT_ROOT_ACTION,
            ),
        )
    return CommandResult(
        ok=False,
        command=policy.command_name,
        message=str(exc),
        data={"status": "error", "error_type": type(exc).__name__},
        checks=checks,
        exit_code=policy.project_root_exit_code,
    )


def _abort_result(*, policy: ErrorMappingPolicy) -> CommandResult:
    message = policy.abort_message or "Aborted."
    return CommandResult(
        ok=False,
        command=policy.command_name,
        message=message,
        data={"status": "aborted", "error_type": "Abort"},
        exit_code=1,
    )


def _failure_result(command_name: str, exc: Exception, *, exit_code: int) -> CommandResult:
    return CommandResult(
        ok=False,
        command=command_name,
        message=str(exc),
        data={"status": "error", "error_type": type(exc).__name__},
        exit_code=exit_code,
    )
