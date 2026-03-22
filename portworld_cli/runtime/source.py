from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.bootstrap.ops import (
    bootstrap_backend_storage,
    check_backend_config,
    export_backend_memory,
    migrate_backend_storage_layout,
)
from backend.bootstrap.runtime import (
    DoctorRuntimeDetails,
    build_backend_storage,
    check_runtime_configuration,
    collect_doctor_runtime_details,
)
from backend.core.settings import Settings, load_environment_files
from backend.realtime.factory import RealtimeProviderFactory
from backend.tools.runtime import RealtimeToolingRuntime
from backend.vision.factory import VisionAnalyzerFactory
from portworld_cli.output import CommandResult, DiagnosticCheck, format_key_value_lines
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
    settings: Settings | None = None
    storage_backend: str | None = None
    storage_details: dict[str, str | bool] | None = None
    storage_paths: dict[str, str] | None = None
    details: DoctorRuntimeDetails | None = None

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
        try:
            settings = _build_settings(paths)
            checks.append(
                DiagnosticCheck(
                    id="settings_loaded",
                    status="pass",
                    message=f"Loaded backend settings from {paths.env_file}",
                )
            )
        except Exception as exc:
            checks.append(
                DiagnosticCheck(
                    id="settings_loaded",
                    status="fail",
                    message=str(exc),
                    action="Fix backend/.env so the CLI can parse the backend settings.",
                )
            )

    storage = None
    if settings is not None:
        try:
            settings.validate_production_posture()
            storage_info, storage = build_backend_storage(settings)
            storage_backend = storage_info.backend
            storage_details = dict(storage_info.details)
            if storage.is_local_backend:
                storage_paths = storage.local_storage_paths().to_dict()
            realtime_provider_factory = RealtimeProviderFactory(settings=settings)
            realtime_provider_factory.validate_configuration()
            checks.append(
                DiagnosticCheck(
                    id="backend_config_valid",
                    status="pass",
                    message=(
                        "Backend config is valid for realtime provider "
                        f"'{realtime_provider_factory.provider_name}' with storage backend "
                        f"'{storage.backend_name}'"
                    ),
                )
            )
        except Exception as exc:
            checks.append(
                DiagnosticCheck(
                    id="backend_config_valid",
                    status="fail",
                    message=str(exc),
                    action="Fix the backend profile or realtime provider settings in backend/.env.",
                )
            )
            storage = None

    vision_valid = False
    tooling_valid = False
    if settings is not None and storage is not None:
        if settings.vision_memory_enabled:
            try:
                vision_factory = VisionAnalyzerFactory(settings=settings)
                vision_factory.validate_configuration()
                checks.append(
                    DiagnosticCheck(
                        id="vision_provider_valid",
                        status="pass",
                        message=f"Vision provider '{vision_factory.provider_name}' is configured correctly",
                    )
                )
                vision_valid = True
            except Exception as exc:
                checks.append(
                    DiagnosticCheck(
                        id="vision_provider_valid",
                        status="fail",
                        message=str(exc),
                        action="Fix the visual-memory provider settings in backend/.env.",
                    )
                )
        else:
            checks.append(
                DiagnosticCheck(
                    id="vision_provider_valid",
                    status="pass",
                    message="Visual memory is disabled",
                )
            )
            vision_valid = True

        if settings.realtime_tooling_enabled:
            try:
                tooling_runtime = RealtimeToolingRuntime.from_settings(
                    settings,
                    storage=storage,
                )
                if tooling_runtime.web_search_enabled:
                    checks.append(
                        DiagnosticCheck(
                            id="tooling_provider_valid",
                            status="pass",
                            message=f"Realtime tooling is enabled with web search provider '{settings.realtime_web_search_provider}'",
                        )
                    )
                else:
                    checks.append(
                        DiagnosticCheck(
                            id="tooling_provider_valid",
                            status="warn",
                            message=(
                                "Realtime tooling is enabled but web_search is unavailable because "
                                "the configured search provider does not have active credentials."
                            ),
                            action=(
                                "Add the required credential for the selected search provider "
                                "and rerun `portworld doctor`."
                            ),
                        )
                    )
                tooling_valid = True
            except Exception as exc:
                checks.append(
                    DiagnosticCheck(
                        id="tooling_provider_valid",
                        status="fail",
                        message=str(exc),
                        action="Fix the realtime tooling settings in backend/.env.",
                    )
                )
        else:
            checks.append(
                DiagnosticCheck(
                    id="tooling_provider_valid",
                    status="pass",
                    message="Realtime tooling is disabled",
                )
            )
            tooling_valid = True

    storage_probe_ran = False
    if full and settings is not None and storage is not None and vision_valid and tooling_valid:
        try:
            storage.bootstrap()
            storage_probe_ran = True
            checks.append(
                DiagnosticCheck(
                    id="storage_bootstrap_probe",
                    status="pass",
                    message="Storage bootstrap probe succeeded",
                )
            )
        except Exception as exc:
            action = (
                "Fix the storage paths and permissions, then rerun `portworld doctor --full`."
                if storage.is_local_backend
                else "Fix the managed database connectivity and runtime storage settings, then rerun `portworld doctor --full`."
            )
            checks.append(
                DiagnosticCheck(
                    id="storage_bootstrap_probe",
                    status="fail",
                    message=str(exc),
                    action=action,
                )
            )
    if settings is not None and storage is not None:
        try:
            details = collect_doctor_runtime_details(
                settings,
                full_readiness=storage_probe_ran,
            )
        except Exception:
            details = None

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
        data["details"] = details.to_dict()
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
    result = check_backend_config(
        _build_settings_for_ops(session),
        full_readiness=full_readiness,
    )
    warnings = tuple(
        DiagnosticCheck(
            id=f"warning-{index}",
            status="warn",
            message=warning,
        )
        for index, warning in enumerate(result.warnings, start=1)
    )
    message = format_key_value_lines(
        ("check_mode", result.check_mode),
        ("storage_backend", result.storage_backend),
        ("realtime_provider", result.realtime_provider),
        ("vision_provider", result.vision_provider),
        ("realtime_tooling_enabled", result.realtime_tooling_enabled),
        ("web_search_provider", result.web_search_provider),
        ("storage_bootstrap_probe", result.storage_bootstrap_probe),
    )
    return CommandResult(
        ok=True,
        command="portworld ops check-config",
        message=message or None,
        data=result.to_dict(),
        checks=warnings,
        exit_code=0,
    )


def run_bootstrap_storage_source(session) -> CommandResult:
    result = bootstrap_backend_storage(_build_settings_for_ops(session))

    payload = {"status": "ok", **result.to_dict()}
    message = format_key_value_lines(
        ("bootstrapped_at_ms", result.bootstrapped_at_ms),
        ("sqlite_path", result.sqlite_path),
        ("user_profile_markdown_path", result.user_profile_markdown_path),
        ("user_profile_json_path", result.user_profile_json_path),
    )
    return CommandResult(
        ok=True,
        command="portworld ops bootstrap-storage",
        message=message or None,
        data=payload,
    )


def run_export_memory_source(
    session,
    *,
    output_path: Path | None,
) -> CommandResult:
    payload = export_backend_memory(
        _build_settings_for_ops(session),
        output_path=output_path,
    )
    message = format_key_value_lines(
        ("artifact_count", payload["artifact_count"]),
        ("export_path", payload["export_path"]),
    )
    return CommandResult(
        ok=True,
        command="portworld ops export-memory",
        message=message or None,
        data=payload,
    )


def run_migrate_storage_layout_source(session) -> CommandResult:
    payload = migrate_backend_storage_layout(_build_settings_for_ops(session))
    message = format_key_value_lines(
        ("migrated_count", payload.get("migrated_count")),
        ("orphaned_count", payload.get("orphaned_count")),
        ("session_ids_scanned", payload.get("session_ids_scanned")),
        ("orphan_root", payload.get("orphan_root")),
    )
    return CommandResult(
        ok=True,
        command="portworld ops migrate-storage-layout",
        message=message or None,
        data=payload,
    )


def _build_settings(paths: ProjectPaths) -> Settings:
    load_environment_files(paths.env_file)
    return Settings.from_env()


def _build_settings_for_ops(session) -> Settings:
    source_session = require_source_workspace_session(
        session,
        command_name="portworld ops",
        usage_error_type=ConfigUsageError,
    )
    assert source_session.project_paths is not None
    paths = source_session.project_paths
    load_environment_files(paths.env_file)
    return Settings.from_env()
