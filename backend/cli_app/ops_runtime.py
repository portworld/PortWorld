from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.bootstrap.memory_export import write_memory_export_zip
from backend.bootstrap.runtime import check_runtime_configuration, build_backend_storage
from backend.cli_app.context import CLIContext
from backend.cli_app.output import CommandResult, DiagnosticCheck, format_key_value_lines
from backend.cli_app.paths import ProjectRootResolutionError
from backend.core.settings import Settings, load_environment_files
from backend.core.storage import now_ms


def _build_settings(cli_context: CLIContext) -> Settings:
    paths = cli_context.resolve_project_paths()
    load_environment_files(paths.env_file)
    return Settings.from_env()


def _failure_result(command: str, exc: Exception) -> CommandResult:
    return CommandResult(
        ok=False,
        command=command,
        message=str(exc),
        data={
            "status": "error",
            "error_type": type(exc).__name__,
        },
        exit_code=1,
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
        result = check_runtime_configuration(
            _build_settings(cli_context),
            full_readiness=full_readiness,
        )
    except ProjectRootResolutionError as exc:
        return _repo_resolution_failure(command, exc)
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
        _, storage = build_backend_storage(_build_settings(cli_context))
        result = storage.bootstrap()
    except ProjectRootResolutionError as exc:
        return _repo_resolution_failure(command, exc)
    except Exception as exc:
        return _failure_result(command, exc)

    payload = {
        "status": "ok",
        "bootstrapped_at_ms": result.bootstrapped_at_ms,
        "sqlite_path": str(result.sqlite_path),
        "user_profile_markdown_path": str(result.user_profile_markdown_path),
        "user_profile_json_path": str(result.user_profile_json_path),
    }
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
        _, storage = build_backend_storage(_build_settings(cli_context))
        storage.bootstrap()
        migration_result = storage.migrate_legacy_storage_layout()
    except ProjectRootResolutionError as exc:
        return _repo_resolution_failure(command, exc)
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
