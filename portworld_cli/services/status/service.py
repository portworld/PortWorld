from __future__ import annotations

from portworld_cli.context import CLIContext
from portworld_cli.output import CommandResult
from portworld_cli.runtime.published import collect_local_runtime_status
from portworld_cli.runtime.reporting import (
    build_health_summary,
    build_status_message,
    collect_live_service_status,
)
from portworld_cli.services.common import ErrorMappingPolicy, map_command_exception
from portworld_cli.workspace.session import load_inspection_session


COMMAND_NAME = "portworld status"
ERROR_POLICY = ErrorMappingPolicy(command_name=COMMAND_NAME)


def run_status(cli_context: CLIContext) -> CommandResult:
    try:
        session = load_inspection_session(cli_context)
    except Exception as exc:
        return map_command_exception(exc, policy=ERROR_POLICY)

    active_target = session.active_target()
    secret_readiness = session.config_session.secret_readiness()
    last_known_payload = session.deploy_state.to_payload() if session.deploy_state.has_data() else None
    deploy_by_target = _build_deploy_by_target_summary(session)
    live_status = collect_live_service_status(session, active_target=active_target)
    local_runtime = collect_local_runtime_status(session)
    health = build_health_summary(session, live_status, local_runtime)
    published_runtime = (
        session.project_config.deploy.published_runtime.to_payload()
        if session.config_session.effective_runtime_source == "published"
        else None
    )

    return CommandResult(
        ok=True,
        command=COMMAND_NAME,
        message=build_status_message(
            session=session,
            active_target=active_target,
            last_known_payload=last_known_payload,
            deploy_by_target=deploy_by_target,
            live_status=live_status,
            local_runtime=local_runtime,
            health=health,
            secret_readiness=secret_readiness,
        ),
        data={
            "workspace_root": str(session.config_session.workspace_root),
            "project_root": (
                None
                if session.config_session.project_paths is None
                else str(session.config_session.project_paths.project_root)
            ),
            "workspace_resolution_source": session.config_session.workspace_resolution_source,
            "active_workspace_root": (
                None
                if session.config_session.active_workspace_root is None
                else str(session.config_session.active_workspace_root)
            ),
            "project_config_path": str(session.config_session.workspace_paths.project_config_file),
            "state_paths": session.config_session.workspace_paths.managed_target_state_paths().status_payload(
                exposed_only=False
            ),
            "project_mode": session.project_config.project_mode,
            "runtime_source": session.project_config.runtime_source,
            "configured_runtime_source": session.config_session.configured_runtime_source,
            "effective_runtime_source": session.config_session.effective_runtime_source,
            "runtime_source_derived_from_legacy": (
                session.config_session.runtime_source_derived_from_legacy
            ),
            "cloud_provider": session.project_config.cloud_provider,
            "active_target": active_target,
            "derived_from_legacy": session.derived_from_legacy,
            "secret_readiness": secret_readiness.to_dict(),
            "published_runtime": published_runtime,
            "local_runtime": None if local_runtime is None else local_runtime.to_payload(),
            "deploy": {
                "source": "state" if last_known_payload else "none",
                "source_target": session.config_session.remembered_deploy_state_target,
                "last_known": last_known_payload,
                "by_target": deploy_by_target,
                "live": live_status.to_payload(),
                "health": health.to_payload(),
            },
        },
        exit_code=0,
    )


def _build_deploy_by_target_summary(session) -> dict[str, dict[str, object | None]]:
    summary: dict[str, dict[str, object | None]] = {}
    for target, state in session.deploy_states_by_target.items():
        state_error = session.deploy_state_errors_by_target.get(target)
        state_payload = state.to_payload() if state.has_data() else None
        summary[target] = {
            "source": (
                "invalid_state"
                if state_error
                else ("state" if state_payload is not None else "none")
            ),
            "state_path": str(session.config_session.workspace_paths.state_file_for_target(target)),
            "last_known": state_payload,
            "state_error": state_error,
        }
    return summary
