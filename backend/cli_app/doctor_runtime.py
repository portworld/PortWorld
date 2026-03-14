from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess

from backend.bootstrap.runtime import (
    DoctorRuntimeDetails,
    build_backend_storage,
    collect_doctor_runtime_details,
)
from backend.cli_app.context import CLIContext
from backend.cli_app.gcp.doctor import evaluate_gcp_cloud_run_readiness
from backend.cli_app.output import CommandResult, DiagnosticCheck, format_key_value_lines
from backend.cli_app.paths import ProjectPaths, ProjectRootResolutionError
from backend.core.settings import Settings, load_environment_files
from backend.realtime.factory import RealtimeProviderFactory
from backend.tools.runtime import RealtimeToolingRuntime
from backend.vision.factory import VisionAnalyzerFactory


COMMAND_NAME = "portworld doctor"


@dataclass(frozen=True, slots=True)
class DoctorOptions:
    target: str
    full: bool
    project: str | None
    region: str | None


def run_doctor(cli_context: CLIContext, options: DoctorOptions) -> CommandResult:
    if options.target == "gcp-cloud-run":
        return _run_gcp_cloud_run_doctor(cli_context, options=options)
    if options.project is not None or options.region is not None:
        return _usage_error_result(
            "--project and --region are only supported with --target gcp-cloud-run."
        )
    return _run_local_doctor(cli_context, full=options.full)


def _run_local_doctor(cli_context: CLIContext, *, full: bool) -> CommandResult:
    checks: list[DiagnosticCheck] = []
    project_root: str | None = None
    settings: Settings | None = None
    storage_backend: str | None = None
    storage_details: dict[str, str | bool] | None = None
    storage_paths: dict[str, str] | None = None
    details: DoctorRuntimeDetails | None = None

    try:
        paths = cli_context.resolve_project_paths()
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
            command=COMMAND_NAME,
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

    docker_result = _probe_command(["docker", "--version"])
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

    compose_result = _probe_command(["docker", "compose", "version"])
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
                            action="Add the search provider credential, for example TAVILY_API_KEY, then rerun `portworld doctor`.",
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
        "project_root": project_root,
        "full": full,
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
        command=COMMAND_NAME,
        message=format_key_value_lines(
            ("target", "local"),
            ("full", full),
            ("project_root", project_root),
            ("storage_backend", storage_backend),
        ),
        data=data,
        checks=tuple(checks),
        exit_code=0 if ok else 1,
    )


def _build_settings(paths: ProjectPaths) -> Settings:
    load_environment_files(paths.env_file)
    return Settings.from_env()


def _run_gcp_cloud_run_doctor(
    cli_context: CLIContext,
    *,
    options: DoctorOptions,
) -> CommandResult:
    try:
        paths = cli_context.resolve_project_paths()
    except ProjectRootResolutionError as exc:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message=format_key_value_lines(
                ("target", options.target),
                ("full", options.full),
                ("project", options.project),
                ("region", options.region),
            ),
            data={
                "target": options.target,
                "project_root": None,
                "full": options.full,
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

    evaluation = evaluate_gcp_cloud_run_readiness(
        paths,
        full=options.full,
        explicit_project=options.project,
        explicit_region=options.region,
    )
    checks = (
        DiagnosticCheck(
            id="project_root_detected",
            status="pass",
            message=f"PortWorld repo detected at {paths.project_root}",
        ),
        *evaluation.checks,
    )
    details = evaluation.details
    return CommandResult(
        ok=evaluation.ok,
        command=COMMAND_NAME,
        message=format_key_value_lines(
            ("target", options.target),
            ("full", options.full),
            ("project_root", paths.project_root),
            ("project", details.project_id or options.project),
            ("region", details.region or options.region),
        ),
        data={
            "target": options.target,
            "project_root": str(paths.project_root),
            "full": options.full,
            "details": details.to_dict(),
        },
        checks=checks,
        exit_code=0 if evaluation.ok else 1,
    )


def _usage_error_result(message: str) -> CommandResult:
    return CommandResult(
        ok=False,
        command=COMMAND_NAME,
        message=message,
        data={
            "status": "error",
            "error_type": "UsageError",
        },
        exit_code=2,
    )


@dataclass(frozen=True, slots=True)
class _ExternalCommandResult:
    ok: bool
    message: str


def _probe_command(command: list[str]) -> _ExternalCommandResult:
    binary = command[0]
    if shutil.which(binary) is None:
        return _ExternalCommandResult(
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
        return _ExternalCommandResult(
            ok=False,
            message=stderr or f"{' '.join(command)} failed with exit code {completed.returncode}",
        )

    output = (completed.stdout or completed.stderr).strip()
    return _ExternalCommandResult(
        ok=True,
        message=output or f"{' '.join(command)} succeeded",
    )
