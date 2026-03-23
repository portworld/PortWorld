from __future__ import annotations

from portworld_cli.context import CLIContext
from portworld_cli.envfile import EnvFileParseError
from portworld_cli.extensions import (
    collect_backend_node_launcher_readiness,
    collect_extensions_summary,
    collect_node_launcher_readiness,
)
from portworld_cli.output import CommandResult
from portworld_cli.runtime.published import collect_published_backend_check_config_payload
from portworld_cli.workspace.project_config import ProjectConfigError, RUNTIME_SOURCE_PUBLISHED
from portworld_cli.services.common import ErrorMappingPolicy, map_command_exception
from portworld_cli.workspace.discovery.paths import ProjectRootResolutionError
from portworld_cli.workspace.state.state_store import CLIStateDecodeError, CLIStateTypeError
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
    extensions_summary = collect_extensions_summary(session)
    host_node_launcher_readiness = collect_node_launcher_readiness(
        session.workspace_paths.extensions_manifest_file
    )
    backend_node_launcher_readiness = None
    if session.effective_runtime_source == RUNTIME_SOURCE_PUBLISHED:
        backend_payload = collect_published_backend_check_config_payload(session.workspace_root)
        backend_node_launcher_readiness = collect_backend_node_launcher_readiness(
            backend_payload.get("extension_health")
        )
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
    extension_lines = [
        message,
        f"extensions_manifest_path: {extensions_summary.manifest_path}",
        f"extensions_python_install_dir: {extensions_summary.python_install_dir}",
        f"extensions_installed_count: {extensions_summary.installed_count}",
        f"extensions_enabled_count: {extensions_summary.enabled_count}",
        f"extensions_error: {extensions_summary.error or 'none'}",
        (
            f"host_node_mcp_enabled_count: {host_node_launcher_readiness.enabled_count}"
            if host_node_launcher_readiness.error is None
            else f"host_node_mcp_error: {host_node_launcher_readiness.error}"
        ),
        (
            "host_node_mcp_missing_binaries: none"
            if host_node_launcher_readiness.error is not None
            or not host_node_launcher_readiness.missing_binaries
            else (
                "host_node_mcp_missing_binaries: "
                f"{', '.join(host_node_launcher_readiness.missing_binaries)}"
            )
        ),
        (
            "host_node_mcp_next_step: none"
            if not host_node_launcher_readiness.bootstrap_required
            else (
                "host_node_mcp_next_step: run `bash install.sh --no-init --non-interactive`, "
                "then rerun `portworld extensions doctor`"
            )
        ),
    ]
    if backend_node_launcher_readiness is not None:
        extension_lines.extend(
            [
                (
                    f"backend_node_mcp_enabled_count: {backend_node_launcher_readiness.enabled_count}"
                    if backend_node_launcher_readiness.error is None
                    else f"backend_node_mcp_error: {backend_node_launcher_readiness.error}"
                ),
                (
                    "backend_node_mcp_missing_binaries: none"
                    if backend_node_launcher_readiness.error is not None
                    or not backend_node_launcher_readiness.missing_binaries
                    else (
                        "backend_node_mcp_missing_binaries: "
                        f"{', '.join(backend_node_launcher_readiness.missing_binaries)}"
                    )
                ),
            ]
        )
    message = "\n".join(extension_lines)
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
            "extensions": extensions_summary.to_payload(),
            "node_mcp": {
                "host": host_node_launcher_readiness.to_payload(),
                "backend": (
                    None
                    if backend_node_launcher_readiness is None
                    else backend_node_launcher_readiness.to_payload()
                ),
            },
        },
        exit_code=0,
    )
