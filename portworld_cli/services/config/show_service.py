from __future__ import annotations

from portworld_cli.context import CLIContext
from portworld_cli.envfile import EnvFileParseError
from portworld_cli.output import CommandResult
from portworld_cli.workspace.project_config import ProjectConfigError, RUNTIME_SOURCE_PUBLISHED
from portworld_cli.services.common import ErrorMappingPolicy, map_command_exception
from portworld_cli.workspace.paths import ProjectRootResolutionError
from portworld_cli.workspace.state_store import CLIStateDecodeError, CLIStateTypeError
from portworld_cli.workspace.session import load_workspace_session

from portworld_cli.services.config.messages import build_config_show_message


def run_config_show(cli_context: CLIContext) -> CommandResult:
    try:
        session = load_workspace_session(cli_context)
    except (
        ProjectRootResolutionError,
        CLIStateDecodeError,
        CLIStateTypeError,
        EnvFileParseError,
        ProjectConfigError,
    ) as exc:
        return map_command_exception(
            exc,
            policy=ErrorMappingPolicy(
                command_name="portworld config show",
                project_root_exit_code=2,
            ),
        )

    secret_readiness = session.secret_readiness()
    config_payload = session.project_config.to_payload()
    published_runtime_payload = (
        session.project_config.deploy.published_runtime.to_payload()
        if session.effective_runtime_source == RUNTIME_SOURCE_PUBLISHED
        else None
    )
    message = build_config_show_message(
        workspace_root=session.workspace_root,
        project_config=session.project_config,
        secret_readiness=secret_readiness,
        project_root=(
            None if session.project_paths is None else session.project_paths.project_root
        ),
        env_path=session.env_path,
        derived_from_legacy=session.derived_from_legacy,
        configured_runtime_source=session.configured_runtime_source,
        effective_runtime_source=session.effective_runtime_source,
        runtime_source_derived_from_legacy=session.runtime_source_derived_from_legacy,
        workspace_resolution_source=session.workspace_resolution_source,
        active_workspace_root=session.active_workspace_root,
    )
    return CommandResult(
        ok=True,
        command="portworld config show",
        message=message,
        data={
            "workspace_root": str(session.workspace_root),
            "project_root": (
                None
                if session.project_paths is None
                else str(session.project_paths.project_root)
            ),
            "project_config_path": str(session.workspace_paths.project_config_file),
            "env_path": None if session.env_path is None else str(session.env_path),
            "compose_path": str(session.workspace_paths.compose_file),
            "project_config": config_payload,
            "secret_readiness": secret_readiness.to_dict(),
            "derived_from_legacy": session.derived_from_legacy,
            "configured_runtime_source": session.configured_runtime_source,
            "effective_runtime_source": session.effective_runtime_source,
            "runtime_source_derived_from_legacy": session.runtime_source_derived_from_legacy,
            "workspace_resolution_source": session.workspace_resolution_source,
            "active_workspace_root": (
                None if session.active_workspace_root is None else str(session.active_workspace_root)
            ),
            "published_runtime": published_runtime_payload,
        },
        exit_code=0,
    )
