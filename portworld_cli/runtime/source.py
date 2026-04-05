from __future__ import annotations

from pathlib import Path
from typing import Any

from portworld_cli.output import CommandResult, DiagnosticCheck, format_key_value_lines
from portworld_cli.runtime.source_backend import (
    build_source_backend_output_path,
    coerce_source_backend_payload,
    run_source_backend_cli,
)
from portworld_cli.runtime.reporting import probe_external_command
from portworld_cli.services.config.errors import ConfigUsageError
from portworld_cli.workspace.discovery.paths import ProjectPaths, ProjectRootResolutionError
from portworld_cli.workspace.session import require_source_workspace_session


def run_local_doctor_source(
    config_session,
    *,
    full: bool,
    command_name: str,
) -> CommandResult:
    checks: list[DiagnosticCheck] = []
    project_root: str | None = None
    storage_backend: str | None = None
    storage_details: dict[str, str | bool] | None = None
    storage_paths: dict[str, str] | None = None
    details: dict[str, object] | None = None

    try:
        config_session = require_source_workspace_session(
            config_session,
            command_name="portworld doctor --target local",
            usage_error_type=ConfigUsageError,
        )
        paths = config_session.project_paths
        assert paths is not None
        project_root = str(paths.project_root)
        checks.append(
            DiagnosticCheck(
                id="project_root_detected",
                status="pass",
                message=f"PortWorld repo detected at {paths.project_root}",
            )
        )
    except ProjectRootResolutionError as exc:
        return CommandResult(
            ok=False,
            command=command_name,
            message=format_key_value_lines(
                ("target", "local"),
                ("full", full),
            ),
            data={
                "target": "local",
                "project_root": None,
                "full": full,
                "status": "error",
                "error_type": type(exc).__name__,
            },
            checks=(
                DiagnosticCheck(
                    id="project_root_detected",
                    status="fail",
                    message=str(exc),
                    action="Run from a PortWorld repo checkout or pass --project-root.",
                ),
            ),
            exit_code=1,
        )

    env_exists = paths.env_file.is_file()
    checks.append(
        DiagnosticCheck(
            id="backend_env_exists",
            status="pass" if env_exists else "fail",
            message=(
                f"{paths.env_file} exists"
                if env_exists
                else "backend/.env is missing"
            ),
            action=None if env_exists else "Run 'portworld init' first",
        )
    )

    docker_result = probe_external_command(["docker", "--version"])
    checks.append(
        DiagnosticCheck(
            id="docker_installed",
            status="pass" if docker_result.ok else "fail",
            message=(
                docker_result.message
                if docker_result.ok
                else docker_result.message or "Docker is not available"
            ),
            action=None if docker_result.ok else "Install Docker Desktop or make `docker` available on PATH.",
        )
    )

    compose_result = probe_external_command(["docker", "compose", "version"])
    checks.append(
        DiagnosticCheck(
            id="docker_compose_available",
            status="pass" if compose_result.ok else "fail",
            message=(
                compose_result.message
                if compose_result.ok
                else compose_result.message or "Docker Compose plugin is not available"
            ),
            action=None if compose_result.ok else "Install or enable the Docker Compose plugin so `docker compose` works.",
        )
    )

    if env_exists:
        checks.append(
            DiagnosticCheck(
                id="settings_loaded",
                status="pass",
                message=f"Using backend settings from {paths.env_file}",
            )
        )
        check_args = ["check-config"]
        if full:
            check_args.append("--full-readiness")
        completed = run_source_backend_cli(paths, backend_args=check_args)
        payload = coerce_source_backend_payload(
            completed,
            default_message="Backend config check did not return structured JSON output.",
        )
        if completed.returncode == 0:
            storage_backend = _coerce_text(payload.get("storage_backend"))
            storage_details = _coerce_mapping(payload.get("storage_details"))
            storage_paths = _coerce_mapping(payload.get("storage_paths"))
            checks.extend(_build_local_backend_checks(payload))
            details_completed = run_source_backend_cli(
                paths,
                backend_args=[
                    "doctor-details",
                    *(["--full-readiness"] if full else []),
                ],
            )
            details_payload = coerce_source_backend_payload(
                details_completed,
                default_message="Backend doctor details did not return structured JSON output.",
            )
            if details_completed.returncode == 0:
                details = dict(details_payload)
                details.pop("status", None)
            elif storage_backend is not None:
                details = {
                    "storage_backend": storage_backend,
                    "storage_details": storage_details or {},
                    **({"storage_paths": storage_paths} if storage_paths is not None else {}),
                }
        else:
            checks.append(
                DiagnosticCheck(
                    id="backend_config_valid",
                    status="fail",
                    message=str(payload.get("message") or "Backend config validation failed."),
                    action="Fix the backend profile or provider settings in backend/.env.",
                )
            )

    ok = not any(check.status == "fail" for check in checks)
    data: dict[str, object] = {
        "target": "local",
        "workspace_root": str(config_session.workspace_root),
        "project_root": project_root,
        "full": full,
        "workspace_resolution_source": config_session.workspace_resolution_source,
        "active_workspace_root": (
            None if config_session.active_workspace_root is None else str(config_session.active_workspace_root)
        ),
    }
    if details is not None:
        data["details"] = details
    elif storage_backend is not None:
        fallback_details: dict[str, object] = {
            "storage_backend": storage_backend,
        }
        if storage_details is not None:
            fallback_details["storage_details"] = storage_details
        if storage_paths is not None:
            fallback_details["storage_paths"] = storage_paths
        data["details"] = fallback_details

    return CommandResult(
        ok=ok,
        command=command_name,
        message=format_key_value_lines(
            ("target", "local"),
            ("full", full),
            ("workspace_root", config_session.workspace_root),
            ("workspace_resolution_source", config_session.workspace_resolution_source),
            ("active_workspace_root", config_session.active_workspace_root),
            ("project_root", project_root),
            ("storage_backend", storage_backend),
        ),
        data=data,
        checks=tuple(checks),
        exit_code=0 if ok else 1,
    )


def run_ops_check_config_source(
    session,
    *,
    full_readiness: bool,
) -> CommandResult:
    paths = _project_paths_for_ops(session)
    backend_args = ["check-config"]
    if full_readiness:
        backend_args.append("--full-readiness")
    completed = run_source_backend_cli(paths, backend_args=backend_args)
    payload = coerce_source_backend_payload(
        completed,
        default_message="Backend config check did not return structured JSON output.",
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


def run_bootstrap_storage_source(session) -> CommandResult:
    completed = run_source_backend_cli(
        _project_paths_for_ops(session),
        backend_args=["bootstrap-storage"],
    )
    payload = coerce_source_backend_payload(
        completed,
        default_message="Backend bootstrap-storage did not return structured JSON output.",
    )
    message = format_key_value_lines(
        ("bootstrapped_at_ms", payload.get("bootstrapped_at_ms")),
        ("sqlite_path", payload.get("sqlite_path")),
        ("user_profile_markdown_path", payload.get("user_profile_markdown_path")),
    )
    return CommandResult(
        ok=completed.returncode == 0,
        command="portworld ops bootstrap-storage",
        message=message or str(payload.get("message") or None),
        data=payload,
        exit_code=0 if completed.returncode == 0 else 1,
    )


def run_export_memory_source(
    session,
    *,
    output_path: Path | None,
) -> CommandResult:
    final_output_path = build_source_backend_output_path(output_path)
    completed = run_source_backend_cli(
        _project_paths_for_ops(session),
        backend_args=["export-memory", "--output", str(final_output_path)],
    )
    payload = coerce_source_backend_payload(
        completed,
        default_message="Backend export-memory did not return structured JSON output.",
    )
    if completed.returncode == 0:
        payload["export_path"] = str(final_output_path)
    message = format_key_value_lines(
        ("artifact_count", payload.get("artifact_count")),
        ("export_path", payload.get("export_path")),
    )
    return CommandResult(
        ok=completed.returncode == 0,
        command="portworld ops export-memory",
        message=message or str(payload.get("message") or None),
        data=payload,
        exit_code=0 if completed.returncode == 0 else 1,
    )


def run_migrate_storage_layout_source(session) -> CommandResult:
    completed = run_source_backend_cli(
        _project_paths_for_ops(session),
        backend_args=["migrate-storage-layout"],
    )
    payload = coerce_source_backend_payload(
        completed,
        default_message="Backend migrate-storage-layout did not return structured JSON output.",
    )
    message = format_key_value_lines(
        ("migrated_count", payload.get("migrated_count")),
        ("orphaned_count", payload.get("orphaned_count")),
        ("session_ids_scanned", payload.get("session_ids_scanned")),
        ("orphan_root", payload.get("orphan_root")),
    )
    return CommandResult(
        ok=completed.returncode == 0,
        command="portworld ops migrate-storage-layout",
        message=message or str(payload.get("message") or None),
        data=payload,
        exit_code=0 if completed.returncode == 0 else 1,
    )


def _project_paths_for_ops(session) -> ProjectPaths:
    source_session = require_source_workspace_session(
        session,
        command_name="portworld ops",
        usage_error_type=ConfigUsageError,
    )
    assert source_session.project_paths is not None
    return source_session.project_paths


def _build_local_backend_checks(payload: dict[str, Any]) -> tuple[DiagnosticCheck, ...]:
    checks: list[DiagnosticCheck] = []
    storage_backend = _coerce_text(payload.get("storage_backend")) or "unknown"
    realtime_provider = _coerce_text(payload.get("realtime_provider")) or "unknown"
    vision_provider = _coerce_text(payload.get("vision_provider"))
    realtime_tooling_enabled = bool(payload.get("realtime_tooling_enabled"))
    web_search_provider = _coerce_text(payload.get("web_search_provider"))
    warnings = tuple(
        warning for warning in payload.get("warnings", ()) if isinstance(warning, str)
    )

    checks.append(
        DiagnosticCheck(
            id="backend_config_valid",
            status="pass",
            message=(
                f"Backend config is valid for realtime provider '{realtime_provider}' "
                f"with storage backend '{storage_backend}'"
            ),
        )
    )
    checks.append(
        DiagnosticCheck(
            id="vision_provider_valid",
            status="pass",
            message=(
                f"Vision provider '{vision_provider}' is configured correctly"
                if vision_provider is not None
                else "Visual memory is disabled"
            ),
        )
    )
    if not realtime_tooling_enabled:
        checks.append(
            DiagnosticCheck(
                id="tooling_provider_valid",
                status="pass",
                message="Realtime tooling is disabled",
            )
        )
    elif any("web_search is disabled" in warning for warning in warnings):
        checks.append(
            DiagnosticCheck(
                id="tooling_provider_valid",
                status="warn",
                message=(
                    "Realtime tooling is enabled but web_search is unavailable because "
                    "the configured search provider does not have active credentials."
                ),
                action=(
                    "Add the required credential for the selected search provider and rerun `portworld doctor`."
                ),
            )
        )
    else:
        checks.append(
            DiagnosticCheck(
                id="tooling_provider_valid",
                status="pass",
                message=(
                    f"Realtime tooling is enabled with web search provider '{web_search_provider}'"
                    if web_search_provider is not None
                    else "Realtime tooling is enabled"
                ),
            )
        )

    if payload.get("storage_bootstrap_probe") is True:
        checks.append(
            DiagnosticCheck(
                id="storage_bootstrap_probe",
                status="pass",
                message="Storage bootstrap probe succeeded",
            )
        )
    return tuple(checks)


def _coerce_mapping(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return dict(value)
    return None


def _coerce_text(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None
