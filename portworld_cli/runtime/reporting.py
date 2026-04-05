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
        env_values = session.config_session.merged_env_values()
        bearer_token = _normalize_text(env_values.get("BACKEND_BEARER_TOKEN"))
        return HealthSummary(
            source="local_probes",
            livez=probe_endpoint(
                base_url,
                "/livez",
                headers=_build_probe_headers(),
            ),
            readyz=probe_endpoint(
                base_url,
                "/readyz",
                headers=_build_probe_headers(
                    bearer_token=bearer_token,
                ),
            ),
        )

    service_ref = live_status.service_ref
    if live_status.status != "ok" or service_ref is None or not service_ref.url:
        return HealthSummary(source="none", livez="unknown", readyz="unknown")

    return HealthSummary(
        source="live_probes",
        livez=probe_endpoint(service_ref.url, "/livez"),
        readyz=probe_endpoint(service_ref.url, "/readyz"),
    )


def probe_endpoint(
    service_url: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
) -> str:
    try:
        response = httpx.get(
            f"{service_url.rstrip('/')}{path}",
            headers=headers,
            timeout=LIVE_PROBE_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        return "unknown"
    return "pass" if response.status_code == 200 else "fail"


def _build_probe_headers(
    *,
    host_header: str | None = None,
    bearer_token: str | None = None,
) -> dict[str, str] | None:
    headers: dict[str, str] = {}
    if host_header:
        headers["Host"] = host_header
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    return headers or None


def _normalize_text(value: object | None) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


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
    pairs: list[tuple[str, object | None]] = [
        ("active_target", active_target or "none"),
        ("service_url", _status_service_url(last_known_payload, live_status)),
        ("deploy", "configured" if last_known_payload else "not_deployed"),
        ("live_service", _humanize_live_status(live_status)),
        ("health", _humanize_health(health)),
        ("credentials", _humanize_credentials(secret_readiness)),
        (
            "realtime",
            _humanize_realtime_provider(secret_readiness.selected_realtime_provider),
        ),
        (
            "vision_memory",
            _humanize_optional_provider(
                enabled=secret_readiness.selected_vision_provider is not None,
                provider_id=secret_readiness.selected_vision_provider,
                suffix="Vision",
            ),
        ),
        (
            "realtime_tooling",
            _humanize_optional_provider(
                enabled=secret_readiness.selected_search_provider is not None,
                provider_id=secret_readiness.selected_search_provider,
                suffix="Search",
            ),
        ),
        ("bearer_token", presence_label(secret_readiness.bearer_token_present)),
    ]

    warning_pairs = _build_status_warning_pairs(
        live_status=live_status,
        local_runtime=local_runtime,
        health=health,
        secret_readiness=secret_readiness,
    )
    if warning_pairs:
        return "\n\n".join(
            [
                format_key_value_lines(*pairs),
                "Warnings\n" + format_key_value_lines(*warning_pairs),
            ]
        )
    return format_key_value_lines(*pairs)


def format_epoch_ms(value: object) -> str | None:
    if not isinstance(value, int):
        return None
    timestamp = datetime.fromtimestamp(value / 1000, tz=UTC)
    return timestamp.isoformat().replace("+00:00", "Z")


def _status_service_url(
    last_known_payload: dict[str, object] | None,
    live_status: LiveServiceStatus,
) -> str | None:
    if live_status.service_ref is not None and live_status.service_ref.url:
        return live_status.service_ref.url
    if last_known_payload is None:
        return None
    value = last_known_payload.get("service_url")
    return value if isinstance(value, str) and value.strip() else None


def _humanize_live_status(live_status: LiveServiceStatus) -> str:
    if live_status.status == "ok":
        return "reachable"
    if live_status.status == "not_found":
        return "not found"
    if live_status.status == "error":
        return "check failed"
    if live_status.status == "skipped":
        return "not checked"
    return live_status.status


def _humanize_health(health: HealthSummary) -> str:
    if health.livez == "pass" and health.readyz == "pass":
        return "healthy"
    if health.livez == "unknown" and health.readyz == "unknown":
        return "unknown"
    parts: list[str] = []
    if health.livez != "pass":
        parts.append(f"livez={health.livez}")
    if health.readyz != "pass":
        parts.append(f"readyz={health.readyz}")
    return ", ".join(parts) or "healthy"


def _humanize_credentials(secret_readiness: SecretReadiness) -> str:
    if secret_readiness.missing_required_secret_keys or secret_readiness.missing_required_config_keys:
        missing = [
            *_humanize_required_keys(secret_readiness.missing_required_secret_keys),
            *_humanize_required_keys(secret_readiness.missing_required_config_keys),
        ]
        return f"missing {', '.join(missing)}"
    return "all required credentials present"


def _humanize_required_keys(keys: tuple[str, ...]) -> list[str]:
    labels: list[str] = []
    for key in keys:
        if "TAVILY" in key:
            labels.append("Tavily search credentials")
        elif key.startswith("VISION_"):
            labels.append("vision provider credentials")
        elif "OPENAI" in key:
            labels.append("OpenAI credentials")
        elif "GEMINI" in key:
            labels.append("Gemini credentials")
        else:
            labels.append(key.lower())
    deduped: list[str] = []
    for label in labels:
        if label not in deduped:
            deduped.append(label)
    return deduped


def _humanize_realtime_provider(provider_id: str) -> str:
    if provider_id == "gemini_live":
        return "Gemini Live"
    if provider_id == "openai":
        return "OpenAI Realtime"
    return provider_id.replace("_", " ").title()


def _humanize_optional_provider(
    *,
    enabled: bool,
    provider_id: str | None,
    suffix: str,
) -> str:
    if not enabled or provider_id is None:
        return "disabled"
    label = provider_id.replace("_", " ").title()
    if suffix.lower() not in label.lower():
        label = f"{label} {suffix}"
    return f"enabled ({label})"


def _build_status_warning_pairs(
    *,
    live_status: LiveServiceStatus,
    local_runtime: LocalRuntimeStatus | None,
    health: HealthSummary,
    secret_readiness: SecretReadiness,
) -> list[tuple[str, object | None]]:
    warnings: list[tuple[str, object | None]] = []
    if live_status.warning_message:
        warnings.append(("live_service", live_status.warning_message))
    if health.livez != "pass":
        warnings.append(("livez", health.livez))
    if health.readyz != "pass":
        warnings.append(("readyz", health.readyz))
    if local_runtime is not None and local_runtime.warning:
        warnings.append(("local_runtime", local_runtime.warning))
    if secret_readiness.missing_required_secret_keys:
        warnings.append(
            ("missing_credentials", ", ".join(_humanize_required_keys(secret_readiness.missing_required_secret_keys)))
        )
    if secret_readiness.missing_required_config_keys:
        warnings.append(
            ("missing_config", ", ".join(_humanize_required_keys(secret_readiness.missing_required_config_keys)))
        )
    return warnings


def presence_label(is_present: bool | None) -> str:
    if is_present is None:
        return "unknown"
    return "present" if is_present else "missing"


def required_presence_label(required: bool, present: bool | None) -> str:
    if not required:
        return "not_required"
    return "present" if present else "missing"


def required_secret_status(secret_readiness: SecretReadiness) -> str:
    if not secret_readiness.required_secret_keys:
        return "none_required"
    parts: list[str] = []
    for key in secret_readiness.required_secret_keys:
        parts.append(f"{key}:{presence_label(secret_readiness.key_presence.get(key))}")
    return ",".join(parts)


def required_config_status(secret_readiness: SecretReadiness) -> str:
    if not secret_readiness.required_config_keys:
        return "none_required"
    parts: list[str] = []
    for key in secret_readiness.required_config_keys:
        parts.append(f"{key}:{presence_label(secret_readiness.config_key_presence.get(key))}")
    return ",".join(parts)
