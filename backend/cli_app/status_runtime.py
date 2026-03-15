from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from backend.cli_app.config_runtime import SecretReadiness
from backend.cli_app.context import CLIContext
from backend.cli_app.gcp import CloudRunServiceRef, GCPAdapters
from backend.cli_app.inspection_runtime import (
    InspectionSession,
    load_inspection_session,
    resolve_gcp_inspection_target,
)
from backend.cli_app.output import CommandResult, format_key_value_lines
from backend.cli_app.paths import ProjectRootResolutionError
from backend.cli_app.project_config import GCP_CLOUD_RUN_TARGET, ProjectConfigError
from backend.cli_app.state import CLIStateDecodeError, CLIStateTypeError
from backend.cli_app.envfile import EnvFileParseError


COMMAND_NAME = "portworld status"
LIVE_PROBE_TIMEOUT_SECONDS = 3.0


@dataclass(frozen=True, slots=True)
class LiveServiceStatus:
    attempted: bool
    status: str
    warning_code: str | None
    warning_message: str | None
    service_exists: bool | None
    service_ref: CloudRunServiceRef | None

    def to_payload(self) -> dict[str, object | None]:
        payload: dict[str, object | None] = {
            "attempted": self.attempted,
            "status": self.status,
            "warning_code": self.warning_code,
            "service_exists": self.service_exists,
        }
        if self.warning_message is not None:
            payload["warning_message"] = self.warning_message
        if self.service_ref is not None:
            payload.update(
                {
                    "project_id": self.service_ref.project_id,
                    "region": self.service_ref.region,
                    "service_name": self.service_ref.service_name,
                    "service_url": self.service_ref.url,
                    "image": self.service_ref.image,
                    "service_account_email": self.service_ref.service_account_email,
                    "ingress": self.service_ref.ingress,
                    "cloudsql_connection_name": self.service_ref.cloudsql_connection_name,
                }
            )
        return payload


@dataclass(frozen=True, slots=True)
class HealthSummary:
    source: str
    livez: str
    readyz: str

    def to_payload(self) -> dict[str, str]:
        return {
            "source": self.source,
            "livez": self.livez,
            "readyz": self.readyz,
        }


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
    health = _build_health_summary(live_status)

    return CommandResult(
        ok=True,
        command=COMMAND_NAME,
        message=_build_status_message(
            session=session,
            active_target=active_target,
            last_known_payload=last_known_payload,
            live_status=live_status,
            health=health,
            secret_readiness=secret_readiness,
        ),
        data={
            "project_root": str(session.config_session.project_paths.project_root),
            "project_config_path": str(session.config_session.project_paths.project_config_file),
            "state_paths": {
                "gcp_cloud_run": str(session.config_session.project_paths.gcp_cloud_run_state_file),
            },
            "project_mode": session.project_config.project_mode,
            "cloud_provider": session.project_config.cloud_provider,
            "active_target": active_target,
            "derived_from_legacy": session.derived_from_legacy,
            "secret_readiness": secret_readiness.to_dict(),
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
    if active_target != GCP_CLOUD_RUN_TARGET:
        return LiveServiceStatus(
            attempted=False,
            status="skipped",
            warning_code=None,
            warning_message=None,
            service_exists=None,
            service_ref=None,
        )

    target = resolve_gcp_inspection_target(session)
    if not target.is_complete():
        return LiveServiceStatus(
            attempted=False,
            status="skipped",
            warning_code="missing_context",
            warning_message="Project, region, or service name is not fully configured for live inspection.",
            service_exists=None,
            service_ref=None,
        )

    adapters = GCPAdapters.create()
    result = adapters.cloud_run.get_service(
        project_id=target.project_id or "",
        region=target.region or "",
        service_name=target.service_name or "",
    )
    if not result.ok:
        error = result.error
        return LiveServiceStatus(
            attempted=True,
            status="error",
            warning_code=error.code if error is not None else "command_failed",
            warning_message=error.message if error is not None else "Live Cloud Run inspection failed.",
            service_exists=None,
            service_ref=None,
        )

    service_ref = result.value
    if service_ref is None:
        return LiveServiceStatus(
            attempted=True,
            status="not_found",
            warning_code=None,
            warning_message=None,
            service_exists=False,
            service_ref=None,
        )

    return LiveServiceStatus(
        attempted=True,
        status="ok",
        warning_code=None,
        warning_message=None,
        service_exists=True,
        service_ref=service_ref,
    )


def _build_health_summary(live_status: LiveServiceStatus) -> HealthSummary:
    service_ref = live_status.service_ref
    if live_status.status != "ok" or service_ref is None or not service_ref.url:
        return HealthSummary(source="none", livez="unknown", readyz="unknown")

    return HealthSummary(
        source="live_probes",
        livez=_probe_endpoint(service_ref.url, "/livez"),
        readyz=_probe_endpoint(service_ref.url, "/readyz"),
    )


def _probe_endpoint(service_url: str, path: str) -> str:
    try:
        response = httpx.get(
            f"{service_url.rstrip('/')}{path}",
            timeout=LIVE_PROBE_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        return "unknown"
    return "pass" if response.status_code == 200 else "fail"


def _build_status_message(
    *,
    session,
    active_target: str | None,
    last_known_payload: dict[str, object] | None,
    live_status: LiveServiceStatus,
    health: HealthSummary,
    secret_readiness: SecretReadiness,
) -> str:
    sections: list[str] = []
    sections.append(
        "\n".join(
            [
                "Project",
                format_key_value_lines(
                    ("project_mode", session.project_config.project_mode),
                    ("cloud_provider", session.project_config.cloud_provider or "none"),
                    ("active_target", active_target or "none"),
                    ("derived_from_legacy", session.derived_from_legacy),
                ),
            ]
        )
    )

    deploy_pairs = [("source", "state" if last_known_payload else "none")]
    if last_known_payload:
        deploy_pairs.extend(
            [
                ("project_id", last_known_payload.get("project_id")),
                ("region", last_known_payload.get("region")),
                ("service_name", last_known_payload.get("service_name")),
                ("service_url", last_known_payload.get("service_url")),
                ("image", last_known_payload.get("image")),
                ("last_deployed_at", _format_epoch_ms(last_known_payload.get("last_deployed_at_ms"))),
            ]
        )
    sections.append("\n".join(["Last deploy", format_key_value_lines(*deploy_pairs)]))

    live_pairs = [
        ("attempted", live_status.attempted),
        ("live_status", live_status.status),
        ("warning_code", live_status.warning_code),
        ("warning_message", live_status.warning_message),
    ]
    if live_status.service_ref is not None:
        live_pairs.extend(
            [
                ("service_exists", live_status.service_exists),
                ("service_name", live_status.service_ref.service_name),
                ("service_url", live_status.service_ref.url),
                ("image", live_status.service_ref.image),
                ("service_account_email", live_status.service_ref.service_account_email),
                ("ingress", live_status.service_ref.ingress),
            ]
        )
    elif live_status.service_exists is not None:
        live_pairs.append(("service_exists", live_status.service_exists))
    sections.append("\n".join(["Live service", format_key_value_lines(*live_pairs)]))

    sections.append(
        "\n".join(
            [
                "Health",
                format_key_value_lines(
                    ("source", health.source),
                    ("livez", health.livez),
                    ("readyz", health.readyz),
                ),
            ]
        )
    )

    sections.append(
        "\n".join(
            [
                "Secrets",
                format_key_value_lines(
                    ("openai_api_key", _presence_label(secret_readiness.openai_api_key_present)),
                    (
                        "vision_provider_api_key",
                        _required_presence_label(
                            secret_readiness.vision_provider_secret_required,
                            secret_readiness.vision_provider_api_key_present,
                        ),
                    ),
                    (
                        "tavily_api_key",
                        _required_presence_label(
                            secret_readiness.tavily_secret_required,
                            secret_readiness.tavily_api_key_present,
                        ),
                    ),
                    ("bearer_token", _presence_label(secret_readiness.bearer_token_present)),
                ),
            ]
        )
    )
    return "\n\n".join(sections)


def _format_epoch_ms(value: object) -> str | None:
    if not isinstance(value, int):
        return None
    timestamp = datetime.fromtimestamp(value / 1000, tz=UTC)
    return timestamp.isoformat().replace("+00:00", "Z")


def _presence_label(is_present: bool) -> str:
    return "present" if is_present else "missing"


def _required_presence_label(required: bool, present: bool | None) -> str:
    if not required:
        return "not_required"
    return "present" if present else "missing"


def _failure_result(exc: Exception, *, exit_code: int) -> CommandResult:
    return CommandResult(
        ok=False,
        command=COMMAND_NAME,
        message=str(exc),
        data={"status": "error", "error_type": type(exc).__name__},
        exit_code=exit_code,
    )
