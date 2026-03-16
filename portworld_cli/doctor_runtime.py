from __future__ import annotations

from dataclasses import dataclass
import shutil
import subprocess

from backend.bootstrap.runtime import (
    DoctorRuntimeDetails,
    build_backend_storage,
    collect_doctor_runtime_details,
)
from portworld_cli.config_runtime import (
    ConfigRuntimeError,
    ensure_source_runtime_session,
    load_config_session,
)
from portworld_cli.context import CLIContext
from portworld_cli.envfile import EnvFileParseError
from portworld_cli.gcp.doctor import evaluate_gcp_cloud_run_readiness
from portworld_cli.output import CommandResult, DiagnosticCheck, format_key_value_lines
from portworld_cli.paths import ProjectPaths, ProjectRootResolutionError
from portworld_cli.published_workspace import (
    build_compose_command,
    coerce_backend_cli_payload,
    run_backend_compose_cli,
)
from portworld_cli.project_config import ProjectConfigError
from portworld_cli.state import CLIStateDecodeError, CLIStateTypeError
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
    try:
        config_session = load_config_session(cli_context)
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
                    action="Run from a PortWorld repo checkout, a published workspace, or pass --project-root.",
                ),
            ),
            exit_code=1,
        )
    except (
        CLIStateDecodeError,
        CLIStateTypeError,
        EnvFileParseError,
        ProjectConfigError,
        ConfigRuntimeError,
    ) as exc:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message=str(exc),
            data={
                "target": "local",
                "project_root": None,
                "full": full,
                "status": "error",
                "error_type": type(exc).__name__,
            },
            exit_code=2,
        )

    if config_session.effective_runtime_source == "published":
        return _run_published_local_doctor(config_session, full=full)

    checks: list[DiagnosticCheck] = []
    project_root: str | None = None
    settings: Settings | None = None
    storage_backend: str | None = None
    storage_details: dict[str, str | bool] | None = None
    storage_paths: dict[str, str] | None = None
    details: DoctorRuntimeDetails | None = None

    try:
        config_session = ensure_source_runtime_session(
            config_session,
            command_name="portworld doctor --target local",
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


def _run_published_local_doctor(config_session, *, full: bool) -> CommandResult:
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

    docker_result = _probe_command(["docker", "--version"])
    checks.append(
        DiagnosticCheck(
            id="docker_installed",
            status="pass" if docker_result.ok else "fail",
            message=docker_result.message,
            action=None if docker_result.ok else "Install Docker Desktop or make `docker` available on PATH.",
        )
    )
    compose_result = _probe_command(["docker", "compose", "version"])
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

    ok = not any(check.status == "fail" for check in checks)
    return CommandResult(
        ok=ok,
        command=COMMAND_NAME,
        message=format_key_value_lines(
            ("target", "local"),
            ("full", full),
            ("workspace_root", workspace_paths.workspace_root),
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
            "env_path": str(workspace_paths.workspace_env_file),
            "compose_path": str(workspace_paths.compose_file),
            "published_runtime": config_session.project_config.deploy.published_runtime.to_payload(),
            "secret_readiness": config_session.secret_readiness().to_dict(),
        },
        checks=tuple(checks),
        exit_code=0 if ok else 1,
    )


def _run_gcp_cloud_run_doctor(
    cli_context: CLIContext,
    *,
    options: DoctorOptions,
) -> CommandResult:
    try:
        session = load_config_session(cli_context)
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
    except (
        CLIStateDecodeError,
        CLIStateTypeError,
        EnvFileParseError,
        ProjectConfigError,
        ConfigRuntimeError,
    ) as exc:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message=str(exc),
            data={
                "target": options.target,
                "workspace_root": None,
                "project_root": None,
                "full": options.full,
                "status": "error",
                "error_type": type(exc).__name__,
            },
            exit_code=2,
        )

    evaluation = evaluate_gcp_cloud_run_readiness(
        source_project_paths=session.project_paths,
        full=options.full,
        explicit_project=options.project,
        explicit_region=options.region,
        project_config=session.project_config,
    )
    root_check = DiagnosticCheck(
        id="workspace_root_detected",
        status="pass",
        message=(
            f"PortWorld source workspace detected at {session.workspace_root}"
            if session.project_paths is not None
            else f"PortWorld published workspace detected at {session.workspace_root}"
        ),
    )
    checks = (root_check, *evaluation.checks)
    details = evaluation.details
    return CommandResult(
        ok=evaluation.ok,
        command=COMMAND_NAME,
        message=format_key_value_lines(
            ("target", options.target),
            ("full", options.full),
            ("workspace_root", session.workspace_root),
            (
                "project_root",
                None if session.project_paths is None else session.project_paths.project_root,
            ),
            ("project", details.project_id or options.project),
            ("region", details.region or options.region),
        ),
        data={
            "target": options.target,
            "workspace_root": str(session.workspace_root),
            "project_root": (
                None
                if session.project_paths is None
                else str(session.project_paths.project_root)
            ),
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
