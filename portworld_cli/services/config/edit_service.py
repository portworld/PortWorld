from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import click

from portworld_cli.context import CLIContext
from portworld_cli.output import CommandResult
from portworld_cli.providers.config import apply_provider_section, collect_provider_section
from portworld_cli.providers.types import ProviderEditOptions
from portworld_cli.workspace.project_config import ProjectConfig
from portworld_cli.workspace.session import WorkspaceSession as ConfigSession
from portworld_cli.workspace.session import load_workspace_session, require_source_workspace_session
from portworld_cli.services.common import ErrorMappingPolicy, map_command_exception

from portworld_cli.services.config.errors import ConfigUsageError
from portworld_cli.services.config.messages import (
    build_init_review_lines,
    build_section_success_message,
)
from portworld_cli.services.config.persistence import (
    preview_secret_readiness,
    write_config_artifacts,
)
from portworld_cli.services.config.sections import (
    apply_cloud_section,
    apply_security_section,
    collect_cloud_section,
    collect_security_section,
)
from portworld_cli.services.config.types import (
    CloudEditOptions,
    SecurityEditOptions,
)


def run_edit_providers(cli_context: CLIContext, options: ProviderEditOptions) -> CommandResult:
    return _run_section_edit(
        cli_context,
        command_name="portworld config edit providers",
        section_name="providers",
        edit_callback=lambda session: _apply_provider_edit(session, options),
    )


def run_edit_security(cli_context: CLIContext, options: SecurityEditOptions) -> CommandResult:
    return _run_section_edit(
        cli_context,
        command_name="portworld config edit security",
        section_name="security",
        edit_callback=lambda session: _apply_security_edit(session, options),
    )


def run_edit_cloud(cli_context: CLIContext, options: CloudEditOptions) -> CommandResult:
    return _run_section_edit(
        cli_context,
        command_name="portworld config edit cloud",
        section_name="cloud",
        edit_callback=lambda session: _apply_cloud_edit(
            session,
            options,
            prompt_defaults_when_local=True,
        ),
    )


def confirm_apply(
    cli_context: CLIContext,
    *,
    command_name: str,
    env_path: Path | None,
    project_config_path: Path,
    summary_lines: tuple[str, ...],
    force: bool = False,
) -> None:
    if cli_context.non_interactive:
        if env_path is not None and env_path.exists() and not force and not cli_context.yes:
            raise ConfigUsageError(
                "backend/.env already exists. Re-run with --force or --yes in non-interactive mode."
            )
        return
    if cli_context.yes:
        return
    prompt_lines = [
        f"Apply {command_name} changes?",
        f"project_config_path: {project_config_path}",
        *summary_lines,
    ]
    if env_path is not None:
        prompt_lines.insert(2, f"env_path: {env_path}")
    confirmed = click.confirm("\n".join(prompt_lines), default=True, show_default=True)
    if not confirmed:
        raise click.Abort()


def _run_section_edit(
    cli_context: CLIContext,
    *,
    command_name: str,
    section_name: str,
    edit_callback: Callable[[ConfigSession], tuple[ProjectConfig, dict[str, str], tuple[str, ...]]],
) -> CommandResult:
    try:
        session = load_workspace_session(cli_context)
        updated_project_config, env_updates, review_lines = edit_callback(session)
        confirm_apply(
            cli_context,
            command_name=command_name,
            env_path=session.env_path,
            project_config_path=session.workspace_paths.project_config_file,
            summary_lines=review_lines,
        )
        outcome = write_config_artifacts(session, updated_project_config, env_updates)
    except Exception as exc:
        return map_command_exception(
            exc,
            policy=ErrorMappingPolicy(
                command_name=command_name,
                abort_message="Aborted before configuration changes were applied.",
            ),
        )

    return CommandResult(
        ok=True,
        command=command_name,
        message=build_section_success_message(
            section_name=section_name,
            project_config=outcome.project_config,
            secret_readiness=outcome.secret_readiness,
            env_path=None if outcome.env_write_result is None else outcome.env_write_result.env_path,
            project_config_path=session.workspace_paths.project_config_file,
            backup_path=None if outcome.env_write_result is None else outcome.env_write_result.backup_path,
        ),
        data={
            "workspace_root": str(session.workspace_root),
            "project_root": (
                None
                if session.project_paths is None
                else str(session.project_paths.project_root)
            ),
            "project_config_path": str(session.workspace_paths.project_config_file),
            "env_path": (
                None
                if outcome.env_write_result is None
                else str(outcome.env_write_result.env_path)
            ),
            "backup_path": (
                str(outcome.env_write_result.backup_path)
                if outcome.env_write_result is not None and outcome.env_write_result.backup_path
                else None
            ),
            "project_config": outcome.project_config.to_payload(),
            "secret_readiness": outcome.secret_readiness.to_dict(),
            "updated_section": section_name,
            "configured_runtime_source": outcome.project_config.runtime_source,
            "effective_runtime_source": outcome.project_config.runtime_source,
        },
        exit_code=0,
    )


def _apply_provider_edit(
    session: ConfigSession,
    options: ProviderEditOptions,
) -> tuple[ProjectConfig, dict[str, str], tuple[str, ...]]:
    session = _ensure_source_runtime_session(
        session,
        command_name="portworld config edit providers",
    )
    provider_result = collect_provider_section(session, options)
    updated_project_config, env_updates = apply_provider_section(
        session.project_config,
        provider_result,
    )
    preview_readiness = preview_secret_readiness(
        session,
        updated_project_config,
        env_updates,
    )
    return updated_project_config, env_updates, build_init_review_lines(
        project_config=updated_project_config,
        secret_readiness=preview_readiness,
    )


def _apply_security_edit(
    session: ConfigSession,
    options: SecurityEditOptions,
) -> tuple[ProjectConfig, dict[str, str], tuple[str, ...]]:
    session = _ensure_source_runtime_session(
        session,
        command_name="portworld config edit security",
    )
    security_result = collect_security_section(session, options)
    updated_project_config, env_updates = apply_security_section(
        session.project_config,
        security_result,
    )
    preview_readiness = preview_secret_readiness(
        session,
        updated_project_config,
        env_updates,
    )
    return updated_project_config, env_updates, build_init_review_lines(
        project_config=updated_project_config,
        secret_readiness=preview_readiness,
    )


def _apply_cloud_edit(
    session: ConfigSession,
    options: CloudEditOptions,
    *,
    prompt_defaults_when_local: bool,
) -> tuple[ProjectConfig, dict[str, str], tuple[str, ...]]:
    cloud_result = collect_cloud_section(
        session,
        options,
        prompt_defaults_when_local=prompt_defaults_when_local,
    )
    updated_project_config, env_updates = apply_cloud_section(
        session.project_config,
        cloud_result,
    )
    preview_readiness = preview_secret_readiness(
        session,
        updated_project_config,
        env_updates,
    )
    return updated_project_config, env_updates, build_init_review_lines(
        project_config=updated_project_config,
        secret_readiness=preview_readiness,
    )


def _ensure_source_runtime_session(
    session: ConfigSession,
    *,
    command_name: str,
) -> ConfigSession:
    return require_source_workspace_session(
        session,
        command_name=command_name,
        usage_error_type=ConfigUsageError,
    )
