from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import time
from typing import Any

from portworld_cli.output import CommandResult, DiagnosticCheck, format_key_value_lines
from portworld_cli.runtime.openclaw import build_openclaw_doctor_checks
from portworld_cli.runtime.reporting import LocalRuntimeStatus, probe_external_command


PUBLISHED_COMPOSE_FILENAME = "docker-compose.yml"


@dataclass(frozen=True, slots=True)
class PublishedComposeStatus:
    available: bool
    running: bool | None
    service_name: str | None
    container_name: str | None
    state: str | None
    health: str | None
    exit_code: int | None
    warning: str | None = None

    def to_payload(self) -> dict[str, object | None]:
        return {
            "available": self.available,
            "running": self.running,
            "service_name": self.service_name,
            "container_name": self.container_name,
            "state": self.state,
            "health": self.health,
            "exit_code": self.exit_code,
            "warning": self.warning,
        }


def build_compose_command(workspace_root: Path, *args: str) -> list[str]:
    return [
        "docker",
        "compose",
        "-f",
        str(workspace_root / PUBLISHED_COMPOSE_FILENAME),
        *args,
    ]


def inspect_published_compose_status(workspace_root: Path) -> PublishedComposeStatus:
    command = build_compose_command(workspace_root, "ps", "--all", "--format", "json")
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            cwd=workspace_root,
        )
    except OSError as exc:
        return PublishedComposeStatus(
            available=False,
            running=None,
            service_name=None,
            container_name=None,
            state=None,
            health=None,
            exit_code=None,
            warning=str(exc),
        )

    if completed.returncode != 0:
        warning = (completed.stderr or completed.stdout).strip() or "docker compose ps failed."
        return PublishedComposeStatus(
            available=False,
            running=None,
            service_name=None,
            container_name=None,
            state=None,
            health=None,
            exit_code=None,
            warning=warning,
        )

    rows = _parse_compose_json_output(completed.stdout)
    backend_row = None
    for row in rows:
        if row.get("Service") == "backend":
            backend_row = row
            break
    if backend_row is None:
        return PublishedComposeStatus(
            available=True,
            running=False,
            service_name="backend",
            container_name=None,
            state="not_created",
            health=None,
            exit_code=None,
        )

    state = _coerce_text(backend_row.get("State"))
    health = _coerce_text(backend_row.get("Health"))
    exit_code = _coerce_int(backend_row.get("ExitCode"))
    return PublishedComposeStatus(
        available=True,
        running=state == "running",
        service_name="backend",
        container_name=_coerce_text(backend_row.get("Name")),
        state=state,
        health=health,
        exit_code=exit_code,
    )


def run_backend_compose_cli(
    workspace_root: Path,
    *,
    backend_args: list[str],
    output_mount: tuple[Path, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = build_compose_command(workspace_root, "run", "--rm")
    if output_mount is not None:
        host_path, container_path = output_mount
        command.extend(["-v", f"{host_path}:{container_path}"])
    command.extend(["backend", "python", "-m", "backend.cli", *backend_args])
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        cwd=workspace_root,
    )


def parse_backend_cli_json(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    stdout = (completed.stdout or "").strip()
    if not stdout:
        raise RuntimeError("Backend compose command did not emit JSON output.")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(stdout) from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Backend compose command returned a non-object JSON payload.")
    return payload


def coerce_backend_cli_payload(
    completed: subprocess.CompletedProcess[str],
    *,
    default_message: str,
) -> dict[str, Any]:
    try:
        return parse_backend_cli_json(completed)
    except RuntimeError:
        message = (completed.stderr or completed.stdout or "").strip() or default_message
        return {
            "status": "error",
            "message": message,
        }


def collect_published_backend_check_config_payload(workspace_root: Path) -> dict[str, Any]:
    completed = run_backend_compose_cli(
        workspace_root,
        backend_args=["check-config"],
    )
    return coerce_backend_cli_payload(
        completed,
        default_message="Containerized backend config check did not return structured JSON output.",
    )


def run_local_doctor_published(
    config_session,
    *,
    full: bool,
    command_name: str,
) -> CommandResult:
    workspace_paths = config_session.workspace_paths
    checks: list[DiagnosticCheck] = [
        DiagnosticCheck(
            id="workspace_root_detected",
            status="pass",
            message=f"PortWorld published workspace detected at {workspace_paths.workspace_root}",
        )
    ]

    env_exists = workspace_paths.workspace_env_file.is_file()
    checks.append(
        DiagnosticCheck(
            id="workspace_env_exists",
            status="pass" if env_exists else "fail",
            message=(
                f"{workspace_paths.workspace_env_file} exists"
                if env_exists
                else "Workspace .env is missing"
            ),
            action=None if env_exists else "Rerun `portworld init --runtime-source published`.",
        )
    )
    compose_exists = workspace_paths.compose_file.is_file()
    checks.append(
        DiagnosticCheck(
            id="workspace_compose_exists",
            status="pass" if compose_exists else "fail",
            message=(
                f"{workspace_paths.compose_file} exists"
                if compose_exists
                else "Workspace docker-compose.yml is missing"
            ),
            action=None if compose_exists else "Rerun `portworld init --runtime-source published`.",
        )
    )

    docker_result = probe_external_command(["docker", "--version"])
    checks.append(
        DiagnosticCheck(
            id="docker_installed",
            status="pass" if docker_result.ok else "fail",
            message=docker_result.message,
            action=None if docker_result.ok else "Install Docker Desktop or make `docker` available on PATH.",
        )
    )
    compose_result = probe_external_command(["docker", "compose", "version"])
    checks.append(
        DiagnosticCheck(
            id="docker_compose_available",
            status="pass" if compose_result.ok else "fail",
            message=compose_result.message,
            action=None if compose_result.ok else "Install or enable the Docker Compose plugin so `docker compose` works.",
        )
    )

    if compose_exists and docker_result.ok and compose_result.ok:
        completed = subprocess.run(
            build_compose_command(workspace_paths.workspace_root, "config", "-q"),
            capture_output=True,
            text=True,
            check=False,
            cwd=workspace_paths.workspace_root,
        )
        checks.append(
            DiagnosticCheck(
                id="workspace_compose_valid",
                status="pass" if completed.returncode == 0 else "fail",
                message=(
                    "docker compose config validation succeeded."
                    if completed.returncode == 0
                    else (completed.stderr or completed.stdout).strip() or "docker compose config failed."
                ),
                action=None if completed.returncode == 0 else "Fix the generated compose file or rerun the published init flow.",
            )
        )

    if full and env_exists and compose_exists and docker_result.ok and compose_result.ok:
        completed = run_backend_compose_cli(
            workspace_paths.workspace_root,
            backend_args=["check-config", "--full-readiness"],
        )
        payload = coerce_backend_cli_payload(
            completed,
            default_message="Containerized backend readiness check did not return structured JSON output.",
        )
        if completed.returncode == 0:
            checks.append(
                DiagnosticCheck(
                    id="published_runtime_full_readiness",
                    status="pass",
                    message=(
                        "Containerized backend readiness check succeeded"
                        + (
                            f" with storage backend '{payload.get('storage_backend')}'."
                            if payload.get("storage_backend")
                            else "."
                        )
                    ),
                )
            )
        else:
            checks.append(
                DiagnosticCheck(
                    id="published_runtime_full_readiness",
                    status="fail",
                    message=str(payload.get("message") or "Containerized backend readiness check failed."),
                    action="Fix the workspace .env or start the workspace container manually to inspect runtime issues.",
                )
            )

    if env_exists:
        checks.extend(
            build_openclaw_doctor_checks(
                env_values=config_session.merged_env_values(),
            )
        )

    ok = not any(check.status == "fail" for check in checks)
    return CommandResult(
        ok=ok,
        command=command_name,
        message=format_key_value_lines(
            ("target", "local"),
            ("full", full),
            ("workspace_root", workspace_paths.workspace_root),
            ("workspace_resolution_source", config_session.workspace_resolution_source),
            ("active_workspace_root", config_session.active_workspace_root),
            ("runtime_source", config_session.effective_runtime_source),
            ("release_tag", config_session.project_config.deploy.published_runtime.release_tag),
            ("image_ref", config_session.project_config.deploy.published_runtime.image_ref),
            ("host_port", config_session.project_config.deploy.published_runtime.host_port),
        ),
        data={
            "target": "local",
            "workspace_root": str(workspace_paths.workspace_root),
            "project_root": None,
            "full": full,
            "runtime_source": config_session.effective_runtime_source,
            "workspace_resolution_source": config_session.workspace_resolution_source,
            "active_workspace_root": (
                None
                if config_session.active_workspace_root is None
                else str(config_session.active_workspace_root)
            ),
            "env_path": str(workspace_paths.workspace_env_file),
            "compose_path": str(workspace_paths.compose_file),
            "published_runtime": config_session.project_config.deploy.published_runtime.to_payload(),
            "secret_readiness": config_session.secret_readiness().to_dict(),
        },
        checks=tuple(checks),
        exit_code=0 if ok else 1,
    )


def run_ops_check_config_published(
    session,
    *,
    full_readiness: bool,
) -> CommandResult:
    backend_args = ["check-config"]
    if full_readiness:
        backend_args.append("--full-readiness")
    completed = run_backend_compose_cli(
        session.workspace_root,
        backend_args=backend_args,
    )
    payload = coerce_backend_cli_payload(
        completed,
        default_message="Containerized backend config check did not return structured JSON output.",
    )
    warnings = tuple(
        DiagnosticCheck(
            id=f"warning-{index}",
            status="warn",
            message=warning,
        )
        for index, warning in enumerate(payload.get("warnings", ()), start=1)
        if isinstance(warning, str)
    )
    return CommandResult(
        ok=completed.returncode == 0,
        command="portworld ops check-config",
        message=format_key_value_lines(
            ("check_mode", payload.get("check_mode")),
            ("storage_backend", payload.get("storage_backend")),
            ("realtime_provider", payload.get("realtime_provider")),
            ("vision_provider", payload.get("vision_provider")),
            ("realtime_tooling_enabled", payload.get("realtime_tooling_enabled")),
            ("web_search_provider", payload.get("web_search_provider")),
            ("storage_bootstrap_probe", payload.get("storage_bootstrap_probe")),
        )
        or str(payload.get("message") or None),
        data=payload,
        checks=warnings,
        exit_code=0 if completed.returncode == 0 else 1,
    )


def run_ops_command_published(
    session,
    *,
    command: str,
    backend_args: list[str],
) -> CommandResult:
    completed = run_backend_compose_cli(
        session.workspace_root,
        backend_args=backend_args,
    )
    payload = coerce_backend_cli_payload(
        completed,
        default_message="Containerized backend command did not return structured JSON output.",
    )
    if payload.get("status") == "ok":
        payload_lines = [(key, value) for key, value in payload.items() if key != "status"]
        message = format_key_value_lines(*payload_lines)
    else:
        message = str(payload.get("message") or payload)
    return CommandResult(
        ok=completed.returncode == 0,
        command=command,
        message=message,
        data=payload,
        exit_code=0 if completed.returncode == 0 else 1,
    )


def run_export_memory_published(
    session,
    *,
    output_path: Path | None,
) -> CommandResult:
    command = "portworld ops export-memory"
    final_output_path = output_path or (Path.cwd() / f"portworld-memory-export-{_now_ms()}.zip")
    final_output_path = final_output_path.resolve()
    final_output_path.parent.mkdir(parents=True, exist_ok=True)
    mount_path = final_output_path.parent
    container_output = f"/host-output/{final_output_path.name}"
    completed = run_backend_compose_cli(
        session.workspace_root,
        backend_args=["export-memory", "--output", container_output],
        output_mount=(mount_path, "/host-output"),
    )
    payload = coerce_backend_cli_payload(
        completed,
        default_message="Containerized memory export did not return structured JSON output.",
    )
    if completed.returncode == 0:
        payload["export_path"] = str(final_output_path)
    return CommandResult(
        ok=completed.returncode == 0,
        command=command,
        message=format_key_value_lines(
            ("artifact_count", payload.get("artifact_count")),
            ("export_path", payload.get("export_path")),
        )
        or str(payload.get("message") or payload),
        data=payload,
        exit_code=0 if completed.returncode == 0 else 1,
    )


def run_memory_maintenance_published(
    session,
    *,
    scope: str,
    session_id: str | None,
    phase: str,
    dry_run: bool,
) -> CommandResult:
    backend_args = ["memory-maintenance-run", "--scope", scope, "--phase", phase]
    if session_id:
        backend_args.extend(["--session-id", session_id])
    if dry_run:
        backend_args.append("--dry-run")
    completed = run_backend_compose_cli(
        session.workspace_root,
        backend_args=backend_args,
    )
    payload = coerce_backend_cli_payload(
        completed,
        default_message="Containerized memory-maintenance-run did not return structured JSON output.",
    )
    message = format_key_value_lines(
        ("scope", payload.get("scope")),
        ("phase", payload.get("phase")),
        ("dry_run", payload.get("dry_run")),
        ("session_id", payload.get("session_id")),
        ("processed_sessions", payload.get("processed_sessions")),
        ("processed_candidates", payload.get("processed_candidates")),
        ("promoted_items", payload.get("promoted_items")),
        ("conflicts", payload.get("conflicts")),
    )
    return CommandResult(
        ok=completed.returncode == 0,
        command="portworld ops memory-maintenance run",
        message=message or str(payload.get("message") or payload),
        data=payload,
        exit_code=0 if completed.returncode == 0 else 1,
    )


def collect_local_runtime_status(session) -> LocalRuntimeStatus | None:
    if session.config_session.effective_runtime_source != "published":
        return None

    compose_status = inspect_published_compose_status(session.config_session.workspace_root)
    return LocalRuntimeStatus(
        available=compose_status.available,
        running=compose_status.running,
        container_name=compose_status.container_name,
        state=compose_status.state,
        health=compose_status.health,
        warning=compose_status.warning,
    )


def _parse_compose_json_output(raw_output: str) -> list[dict[str, object]]:
    text = raw_output.strip()
    if not text:
        return []
    if text.startswith("["):
        payload = json.loads(text)
        return [item for item in payload if isinstance(item, dict)]
    rows: list[dict[str, object]] = []
    for line in text.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def _coerce_text(value: object) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def _coerce_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _now_ms() -> int:
    return int(time.time() * 1000)
