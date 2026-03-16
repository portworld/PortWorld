from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.bootstrap.memory_export import write_memory_export_zip
from backend.bootstrap.runtime import check_runtime_configuration, build_backend_storage
from portworld_cli.config_runtime import (
    ConfigRuntimeError,
    ConfigSession,
    ensure_source_runtime_session,
    load_config_session,
)
from portworld_cli.context import CLIContext
from portworld_cli.output import CommandResult, DiagnosticCheck, format_key_value_lines
from portworld_cli.paths import ProjectRootResolutionError
from portworld_cli.published_workspace import coerce_backend_cli_payload, run_backend_compose_cli
from backend.core.settings import Settings, load_environment_files
from backend.core.storage import now_ms


def _build_settings(cli_context: CLIContext) -> Settings:
    ensure_source_runtime_session(
        load_config_session(cli_context),
        command_name="portworld ops",
    )
    paths = cli_context.resolve_project_paths()
    load_environment_files(paths.env_file)
    return Settings.from_env()


def _load_runtime_session(cli_context: CLIContext) -> ConfigSession:
    return load_config_session(cli_context)


def _failure_result(command: str, exc: Exception, *, exit_code: int = 1) -> CommandResult:
    return CommandResult(
        ok=False,
        command=command,
        message=str(exc),
        data={
            "status": "error",
            "error_type": type(exc).__name__,
        },
        exit_code=exit_code,
    )


def _repo_resolution_failure(command: str, exc: ProjectRootResolutionError) -> CommandResult:
    return CommandResult(
        ok=False,
        command=command,
        message=str(exc),
        data={
            "status": "error",
            "error_type": type(exc).__name__,
        },
        checks=(
            DiagnosticCheck(
                id="project-root",
                status="fail",
                message=str(exc),
                action="Run from a PortWorld repo checkout or pass --project-root.",
            ),
        ),
        exit_code=1,
    )


def run_check_config(cli_context: CLIContext, *, full_readiness: bool) -> CommandResult:
    command = "portworld ops check-config"
    try:
        session = _load_runtime_session(cli_context)
        if session.effective_runtime_source == "published":
            return _run_published_ops_check_config(session, full_readiness=full_readiness)
        result = check_runtime_configuration(
            _build_settings(cli_context),
            full_readiness=full_readiness,
        )
    except ProjectRootResolutionError as exc:
        return _repo_resolution_failure(command, exc)
    except ConfigRuntimeError as exc:
        return _failure_result(command, exc, exit_code=2)
    except Exception as exc:
        return _failure_result(command, exc)

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
        command=command,
        message=message or None,
        data=result.to_dict(),
        checks=warnings,
        exit_code=0,
    )


def run_bootstrap_storage(cli_context: CLIContext) -> CommandResult:
    command = "portworld ops bootstrap-storage"
    try:
        session = _load_runtime_session(cli_context)
        if session.effective_runtime_source == "published":
            return _run_published_ops_command(
                session,
                command=command,
                backend_args=["bootstrap-storage"],
            )
        settings = _build_settings(cli_context)
        _, storage = build_backend_storage(settings)
        if not storage.is_local_backend:
            raise RuntimeError(
                "portworld ops bootstrap-storage is only supported when "
                "BACKEND_STORAGE_BACKEND=local. Managed metadata bootstrap now runs through "
                "`portworld ops check-config --full` or normal runtime startup instead."
            )
        result = storage.bootstrap()
    except ProjectRootResolutionError as exc:
        return _repo_resolution_failure(command, exc)
    except ConfigRuntimeError as exc:
        return _failure_result(command, exc, exit_code=2)
    except Exception as exc:
        return _failure_result(command, exc)

    payload = {"status": "ok", **result.to_dict()}
    message = format_key_value_lines(
        ("bootstrapped_at_ms", result.bootstrapped_at_ms),
        ("sqlite_path", result.sqlite_path),
        ("user_profile_markdown_path", result.user_profile_markdown_path),
        ("user_profile_json_path", result.user_profile_json_path),
    )
    return CommandResult(
        ok=True,
        command=command,
        message=message or None,
        data=payload,
    )


def run_export_memory(cli_context: CLIContext, *, output_path: Path | None) -> CommandResult:
    command = "portworld ops export-memory"
    try:
        session = _load_runtime_session(cli_context)
        if session.effective_runtime_source == "published":
            return _run_published_export_memory(session, output_path=output_path)
        settings = _build_settings(cli_context)
        _, storage = build_backend_storage(settings)
        storage.bootstrap()
        artifacts = storage.list_memory_export_artifacts()
        final_output_path = output_path or (Path.cwd() / f"portworld-memory-export-{now_ms()}.zip")
        export_path = write_memory_export_zip(
            artifacts=artifacts,
            session_retention_days=settings.backend_session_memory_retention_days,
            output_path=final_output_path,
        )
    except ProjectRootResolutionError as exc:
        return _repo_resolution_failure(command, exc)
    except ConfigRuntimeError as exc:
        return _failure_result(command, exc, exit_code=2)
    except Exception as exc:
        return _failure_result(command, exc)

    payload = {
        "status": "ok",
        "artifact_count": len(artifacts),
        "export_path": str(export_path),
    }
    message = format_key_value_lines(
        ("artifact_count", len(artifacts)),
        ("export_path", export_path),
    )
    return CommandResult(
        ok=True,
        command=command,
        message=message or None,
        data=payload,
    )


def run_migrate_storage_layout(cli_context: CLIContext) -> CommandResult:
    command = "portworld ops migrate-storage-layout"
    try:
        session = _load_runtime_session(cli_context)
        if session.effective_runtime_source == "published":
            return _run_published_ops_command(
                session,
                command=command,
                backend_args=["migrate-storage-layout"],
            )
        settings = _build_settings(cli_context)
        _, storage = build_backend_storage(settings)
        if not storage.is_local_backend:
            raise RuntimeError(
                "portworld ops migrate-storage-layout is only supported when "
                "BACKEND_STORAGE_BACKEND=local."
            )
        storage.bootstrap()
        migration_result = storage.migrate_legacy_storage_layout()
    except ProjectRootResolutionError as exc:
        return _repo_resolution_failure(command, exc)
    except ConfigRuntimeError as exc:
        return _failure_result(command, exc, exit_code=2)
    except Exception as exc:
        return _failure_result(command, exc)

    payload: dict[str, Any] = {"status": "ok", **migration_result}
    message = format_key_value_lines(
        ("migrated_count", migration_result.get("migrated_count")),
        ("orphaned_count", migration_result.get("orphaned_count")),
        ("session_ids_scanned", migration_result.get("session_ids_scanned")),
        ("orphan_root", migration_result.get("orphan_root")),
    )
    return CommandResult(
        ok=True,
        command=command,
        message=message or None,
        data=payload,
    )


def _run_published_ops_check_config(
    session: ConfigSession,
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


def _run_published_ops_command(
    session: ConfigSession,
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
    message = None
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


def _run_published_export_memory(
    session: ConfigSession,
    *,
    output_path: Path | None,
) -> CommandResult:
    command = "portworld ops export-memory"
    final_output_path = output_path or (Path.cwd() / f"portworld-memory-export-{now_ms()}.zip")
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
