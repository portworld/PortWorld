from __future__ import annotations

from portworld_cli.config_runtime import SecretReadiness
from portworld_cli.context import CLIContext
from portworld_cli.inspection_runtime import InspectionSession, load_inspection_session
from portworld_cli.output import CommandResult
from portworld_cli.paths import ProjectRootResolutionError
from portworld_cli.project_config import ProjectConfigError
from portworld_cli.runtime.published import collect_local_runtime_status
from portworld_cli.runtime.reporting import (
    LIVE_PROBE_TIMEOUT_SECONDS,
    HealthSummary,
    LiveServiceStatus,
    LocalRuntimeStatus,
    build_health_summary,
    build_status_message,
    collect_live_service_status,
    format_epoch_ms,
    presence_label,
    probe_endpoint,
    required_presence_label,
)
from portworld_cli.state import CLIStateDecodeError, CLIStateTypeError
from portworld_cli.envfile import EnvFileParseError


COMMAND_NAME = "portworld status"


def run_status(cli_context: CLIContext) -> CommandResult:
    try:
        session = load_inspection_session(cli_context)
    except ProjectRootResolutionError as exc:
        return _failure_result(exc, exit_code=1)
    except (
        CLIStateDecodeError,
        CLIStateTypeError,
        EnvFileParseError,
        ProjectConfigError,
    ) as exc:
        return _failure_result(exc, exit_code=2)

    active_target = session.active_target()
    secret_readiness = session.config_session.secret_readiness()
    last_known_payload = session.deploy_state.to_payload() if session.deploy_state.has_data() else None
    live_status = _collect_live_service_status(session, active_target=active_target)
    local_runtime = _collect_local_runtime_status(session)
    health = _build_health_summary(session, live_status, local_runtime)
    published_runtime = (
        session.project_config.deploy.published_runtime.to_payload()
        if session.config_session.effective_runtime_source == "published"
        else None
    )

    return CommandResult(
        ok=True,
        command=COMMAND_NAME,
        message=_build_status_message(
            session=session,
            active_target=active_target,
            last_known_payload=last_known_payload,
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
            "state_paths": {
                "gcp_cloud_run": str(session.config_session.workspace_paths.gcp_cloud_run_state_file),
            },
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
                "last_known": last_known_payload,
                "live": live_status.to_payload(),
                "health": health.to_payload(),
            },
        },
        exit_code=0,
    )


def _collect_live_service_status(
    session: InspectionSession,
    *,
    active_target: str | None,
) -> LiveServiceStatus:
    return collect_live_service_status(session, active_target=active_target)


def _build_health_summary(
    session: InspectionSession,
    live_status: LiveServiceStatus,
    local_runtime: LocalRuntimeStatus | None,
) -> HealthSummary:
    return build_health_summary(session, live_status, local_runtime)


def _collect_local_runtime_status(session: InspectionSession) -> LocalRuntimeStatus | None:
    return collect_local_runtime_status(session)


def _probe_endpoint(service_url: str, path: str) -> str:
    return probe_endpoint(service_url, path)


def _build_status_message(
    *,
    session: InspectionSession,
    active_target: str | None,
    last_known_payload: dict[str, object] | None,
    live_status: LiveServiceStatus,
    local_runtime: LocalRuntimeStatus | None,
    health: HealthSummary,
    secret_readiness: SecretReadiness,
) -> str:
    return build_status_message(
        session=session,
        active_target=active_target,
        last_known_payload=last_known_payload,
        live_status=live_status,
        local_runtime=local_runtime,
        health=health,
        secret_readiness=secret_readiness,
    )


def _failure_result(exc: Exception, *, exit_code: int) -> CommandResult:
    return CommandResult(
        ok=False,
        command=COMMAND_NAME,
        message=str(exc),
        data={"status": "error", "error_type": type(exc).__name__},
        exit_code=exit_code,
    )


def _format_epoch_ms(value: object) -> str | None:
    return format_epoch_ms(value)


def _presence_label(is_present: bool | None) -> str:
    return presence_label(is_present)


def _required_presence_label(required: bool, present: bool | None) -> str:
    return required_presence_label(required, present)
