from __future__ import annotations

from pathlib import Path

from portworld_cli.context import CLIContext
from portworld_cli.output import CommandResult
from portworld_cli.runtime.published import (
    run_export_memory_published,
    run_ops_check_config_published,
    run_ops_command_published,
)
from portworld_cli.runtime.source import (
    run_bootstrap_storage_source,
    run_export_memory_source,
    run_migrate_storage_layout_source,
    run_ops_check_config_source,
)
from portworld_cli.services.common import ErrorMappingPolicy, map_command_exception
from portworld_cli.services.config.errors import ConfigRuntimeError
from portworld_cli.workspace.session import WorkspaceSession as ConfigSession
from portworld_cli.workspace.session import load_workspace_session


def _load_runtime_session(cli_context: CLIContext) -> ConfigSession:
    return load_workspace_session(cli_context)


def _error_result(command: str, exc: Exception) -> CommandResult:
    return map_command_exception(
        exc,
        policy=ErrorMappingPolicy(
            command_name=command,
            project_root_check_id="project-root",
        ),
        exit_code_2_types=(ConfigRuntimeError,),
        include_common_exit_code_2=False,
    )


def run_check_config(cli_context: CLIContext, *, full_readiness: bool) -> CommandResult:
    command = "portworld ops check-config"
    try:
        session = _load_runtime_session(cli_context)
        if session.effective_runtime_source == "published":
            return _run_published_ops_check_config(session, full_readiness=full_readiness)
        return run_ops_check_config_source(session, full_readiness=full_readiness)
    except Exception as exc:
        return _error_result(command, exc)


def run_bootstrap_storage(cli_context: CLIContext) -> CommandResult:
    command = "portworld ops bootstrap-storage"
    try:
        session = _load_runtime_session(cli_context)
        if session.effective_runtime_source == "published":
            return _run_published_ops_command(
                session,
                command=command,
                backend_args=["bootstrap-storage"],
            )
        return run_bootstrap_storage_source(session)
    except Exception as exc:
        return _error_result(command, exc)


def run_export_memory(cli_context: CLIContext, *, output_path: Path | None) -> CommandResult:
    command = "portworld ops export-memory"
    try:
        session = _load_runtime_session(cli_context)
        if session.effective_runtime_source == "published":
            return _run_published_export_memory(session, output_path=output_path)
        return run_export_memory_source(session, output_path=output_path)
    except Exception as exc:
        return _error_result(command, exc)


def run_migrate_storage_layout(cli_context: CLIContext) -> CommandResult:
    command = "portworld ops migrate-storage-layout"
    try:
        session = _load_runtime_session(cli_context)
        if session.effective_runtime_source == "published":
            return _run_published_ops_command(
                session,
                command=command,
                backend_args=["migrate-storage-layout"],
            )
        return run_migrate_storage_layout_source(session)
    except Exception as exc:
        return _error_result(command, exc)


def _run_published_ops_check_config(
    session: ConfigSession,
    *,
    full_readiness: bool,
) -> CommandResult:
    return run_ops_check_config_published(session, full_readiness=full_readiness)


def _run_published_ops_command(
    session: ConfigSession,
    *,
    command: str,
    backend_args: list[str],
) -> CommandResult:
    return run_ops_command_published(
        session,
        command=command,
        backend_args=backend_args,
    )


def _run_published_export_memory(
    session: ConfigSession,
    *,
    output_path: Path | None,
) -> CommandResult:
    return run_export_memory_published(session, output_path=output_path)
