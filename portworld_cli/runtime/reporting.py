from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import shutil
import subprocess
from typing import Any, Mapping

import httpx

from portworld_cli.gcp import CloudRunServiceRef, GCPAdapters
from portworld_cli.workspace.session import (
    InspectionSession,
    SecretReadiness,
    resolve_gcp_inspection_target,
)
from portworld_cli.output import format_key_value_lines
from portworld_cli.targets import TARGET_GCP_CLOUD_RUN


LIVE_PROBE_TIMEOUT_SECONDS = 3.0


@dataclass(frozen=True, slots=True)
class ExternalCommandResult:
    ok: bool
    message: str


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


@dataclass(frozen=True, slots=True)
class LocalRuntimeStatus:
    available: bool
    running: bool | None
    container_name: str | None
    state: str | None
    health: str | None
    warning: str | None

    def to_payload(self) -> dict[str, object | None]:
        return {
            "available": self.available,
            "running": self.running,
            "container_name": self.container_name,
            "state": self.state,
            "health": self.health,
            "warning": self.warning,
        }


def probe_external_command(command: list[str]) -> ExternalCommandResult:
    binary = command[0]
    if shutil.which(binary) is None:
        return ExternalCommandResult(
            ok=False,
            message=f"{binary} is not installed or not on PATH",
        )

    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout).strip()
        return ExternalCommandResult(
            ok=False,
            message=stderr or f"{' '.join(command)} failed with exit code {completed.returncode}",
        )

    output = (completed.stdout or completed.stderr).strip()
    return ExternalCommandResult(
        ok=True,
        message=output or f"{' '.join(command)} succeeded",
    )


def collect_live_service_status(
    session: InspectionSession,
    *,
    active_target: str | None,
) -> LiveServiceStatus:
    if active_target != TARGET_GCP_CLOUD_RUN:
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


def build_health_summary(
    session: InspectionSession,
    live_status: LiveServiceStatus,
    local_runtime: LocalRuntimeStatus | None,
) -> HealthSummary:
    if session.config_session.effective_runtime_source == "published":
        if local_runtime is None or not local_runtime.available:
            return HealthSummary(source="none", livez="unknown", readyz="unknown")

        host_port = session.project_config.deploy.published_runtime.host_port
        base_url = f"http://127.0.0.1:{host_port}"
        return HealthSummary(
            source="local_probes",
            livez=probe_endpoint(base_url, "/livez"),
            readyz=probe_endpoint(base_url, "/readyz"),
        )

    service_ref = live_status.service_ref
    if live_status.status != "ok" or service_ref is None or not service_ref.url:
        return HealthSummary(source="none", livez="unknown", readyz="unknown")

    return HealthSummary(
        source="live_probes",
        livez=probe_endpoint(service_ref.url, "/livez"),
        readyz=probe_endpoint(service_ref.url, "/readyz"),
    )


def probe_endpoint(service_url: str, path: str) -> str:
    try:
        response = httpx.get(
            f"{service_url.rstrip('/')}{path}",
            timeout=LIVE_PROBE_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        return "unknown"
    return "pass" if response.status_code == 200 else "fail"


def build_status_message(
    *,
    session: InspectionSession,
    active_target: str | None,
    last_known_payload: dict[str, object] | None,
    deploy_by_target: Mapping[str, Mapping[str, Any]],
    live_status: LiveServiceStatus,
    local_runtime: LocalRuntimeStatus | None,
    health: HealthSummary,
    secret_readiness: SecretReadiness,
) -> str:
    sections: list[str] = []
    project_pairs: list[tuple[str, object | None]] = [
        ("workspace_root", session.config_session.workspace_root),
        (
            "project_root",
            None
            if session.config_session.project_paths is None
            else session.config_session.project_paths.project_root,
        ),
        (
            "workspace_resolution_source",
            session.config_session.workspace_resolution_source,
        ),
        ("active_workspace_root", session.config_session.active_workspace_root),
        ("project_mode", session.project_config.project_mode),
        ("runtime_source", session.project_config.runtime_source or "unset"),
        (
            "effective_runtime_source",
            session.config_session.effective_runtime_source,
        ),
        ("cloud_provider", session.project_config.cloud_provider or "none"),
        ("active_target", active_target or "none"),
        ("derived_from_legacy", session.derived_from_legacy),
    ]
    if session.config_session.effective_runtime_source == "published":
        project_pairs[5:5] = [
            (
                "release_tag",
                session.project_config.deploy.published_runtime.release_tag,
            ),
            ("image_ref", session.project_config.deploy.published_runtime.image_ref),
            ("host_port", session.project_config.deploy.published_runtime.host_port),
        ]
    sections.append(
        "\n".join(
            [
                "Project",
                format_key_value_lines(*project_pairs),
            ]
        )
    )

    if local_runtime is not None:
        sections.append(
            "\n".join(
                [
                    "Local runtime",
                    format_key_value_lines(
                        ("available", local_runtime.available),
                        ("running", local_runtime.running),
                        ("state", local_runtime.state),
                        ("health", local_runtime.health),
                        ("container_name", local_runtime.container_name),
                        ("warning", local_runtime.warning),
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
                ("runtime_source", last_known_payload.get("runtime_source")),
                ("image_source_mode", last_known_payload.get("image_source_mode")),
                ("published_release_tag", last_known_payload.get("published_release_tag")),
                ("published_image_ref", last_known_payload.get("published_image_ref")),
                ("service_url", last_known_payload.get("service_url")),
                ("image", last_known_payload.get("image")),
                ("last_deployed_at", format_epoch_ms(last_known_payload.get("last_deployed_at_ms"))),
            ]
        )
    sections.append("\n".join(["Last deploy", format_key_value_lines(*deploy_pairs)]))

    by_target_pairs: list[tuple[str, object | None]] = []
    for target, target_summary in deploy_by_target.items():
        source = target_summary.get("source")
        last_known = target_summary.get("last_known")
        service_url = None
        if isinstance(last_known, dict):
            service_url = last_known.get("service_url")
        if service_url:
            by_target_pairs.append((target, f"{source} ({service_url})"))
        else:
            by_target_pairs.append((target, source))
    sections.append("\n".join(["Deploy by target", format_key_value_lines(*by_target_pairs)]))

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
                    ("openai_api_key", presence_label(secret_readiness.openai_api_key_present)),
                    (
                        "vision_provider_api_key",
                        required_presence_label(
                            secret_readiness.vision_provider_secret_required,
                            secret_readiness.vision_provider_api_key_present,
                        ),
                    ),
                    (
                        "tavily_api_key",
                        required_presence_label(
                            secret_readiness.tavily_secret_required,
                            secret_readiness.tavily_api_key_present,
                        ),
                    ),
                    ("bearer_token", presence_label(secret_readiness.bearer_token_present)),
                ),
            ]
        )
    )
    return "\n\n".join(sections)


def format_epoch_ms(value: object) -> str | None:
    if not isinstance(value, int):
        return None
    timestamp = datetime.fromtimestamp(value / 1000, tz=UTC)
    return timestamp.isoformat().replace("+00:00", "Z")


def presence_label(is_present: bool | None) -> str:
    if is_present is None:
        return "unknown"
    return "present" if is_present else "missing"


def required_presence_label(required: bool, present: bool | None) -> str:
    if not required:
        return "not_required"
    return "present" if present else "missing"
