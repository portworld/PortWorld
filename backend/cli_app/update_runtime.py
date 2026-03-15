from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess

from backend import __version__
from backend.cli_app.context import CLIContext
from backend.cli_app.deploy_runtime import DeployGCPCloudRunOptions, run_deploy_gcp_cloud_run
from backend.cli_app.envfile import EnvFileParseError
from backend.cli_app.inspection_runtime import load_inspection_session
from backend.cli_app.output import CommandResult, format_key_value_lines
from backend.cli_app.paths import ProjectPaths, ProjectRootResolutionError, resolve_project_paths
from backend.cli_app.project_config import GCP_CLOUD_RUN_TARGET, ProjectConfigError
from backend.cli_app.state import CLIStateDecodeError, CLIStateTypeError


UPDATE_CLI_COMMAND_NAME = "portworld update cli"
UPDATE_DEPLOY_COMMAND_NAME = "portworld update deploy"
WRAPPED_DEPLOY_COMMAND = "portworld deploy gcp-cloud-run"
SELF_HOST_DOCS_HINT = "See backend/README.md and docs/BACKEND_SELF_HOSTING.md."


class UpdateUsageError(RuntimeError):
    pass


def run_update_cli(cli_context: CLIContext) -> CommandResult:
    repo_paths = _try_resolve_repo_paths(cli_context)
    detected_mode, recommended_commands = _detect_cli_update_mode(repo_paths)
    message_lines = [
        format_key_value_lines(
            ("current_version", __version__),
            ("detected_install_mode", detected_mode),
        )
    ]
    if repo_paths is not None:
        message_lines.append(f"repo_root: {repo_paths.project_root}")
    message_lines.append("recommended_commands:")
    for command in recommended_commands:
        message_lines.append(f"- {command}")
    message_lines.append(SELF_HOST_DOCS_HINT)

    return CommandResult(
        ok=True,
        command=UPDATE_CLI_COMMAND_NAME,
        message="\n".join(message_lines),
        data={
            "current_version": __version__,
            "detected_install_mode": detected_mode,
            "recommended_commands": recommended_commands,
            "repo_root": str(repo_paths.project_root) if repo_paths is not None else None,
            "docs_hint": SELF_HOST_DOCS_HINT,
        },
        exit_code=0,
    )


def run_update_deploy(
    cli_context: CLIContext,
    options: DeployGCPCloudRunOptions,
) -> CommandResult:
    try:
        session = load_inspection_session(cli_context)
    except ProjectRootResolutionError as exc:
        return _failure_result(UPDATE_DEPLOY_COMMAND_NAME, exc, exit_code=1)
    except (
        CLIStateDecodeError,
        CLIStateTypeError,
        EnvFileParseError,
        ProjectConfigError,
    ) as exc:
        return _failure_result(UPDATE_DEPLOY_COMMAND_NAME, exc, exit_code=2)

    active_target = session.active_target()
    if active_target is None:
        return _failure_result(
            UPDATE_DEPLOY_COMMAND_NAME,
            UpdateUsageError(
                "No managed deploy target is configured. Use `portworld deploy gcp-cloud-run` first "
                "or configure managed cloud defaults with `portworld config edit cloud`."
            ),
            exit_code=2,
        )
    if active_target != GCP_CLOUD_RUN_TARGET:
        return _failure_result(
            UPDATE_DEPLOY_COMMAND_NAME,
            UpdateUsageError(f"Managed deploy target '{active_target}' is not supported yet."),
            exit_code=2,
        )

    result = run_deploy_gcp_cloud_run(cli_context, options)
    wrapped_message = result.message
    prefix = "Managed redeploy target: gcp-cloud-run"
    if wrapped_message:
        wrapped_message = f"{prefix}\n\n{wrapped_message}"
    else:
        wrapped_message = prefix
    wrapped_data = dict(result.data)
    wrapped_data["target"] = GCP_CLOUD_RUN_TARGET
    wrapped_data["wrapped_command"] = WRAPPED_DEPLOY_COMMAND
    return replace(
        result,
        command=UPDATE_DEPLOY_COMMAND_NAME,
        message=wrapped_message,
        data=wrapped_data,
    )


def _try_resolve_repo_paths(cli_context: CLIContext) -> ProjectPaths | None:
    try:
        return resolve_project_paths(
            explicit_root=cli_context.project_root_override,
            start=Path.cwd(),
        )
    except ProjectRootResolutionError:
        return None


def _detect_cli_update_mode(repo_paths: ProjectPaths | None) -> tuple[str, list[str]]:
    if repo_paths is not None and _looks_like_source_checkout(repo_paths.project_root):
        return "source_checkout", ["pipx install . --force"]

    pipx_install = _detect_pipx_install()
    if pipx_install:
        return "pipx", ["pipx upgrade portworld"]

    commands = ["pipx upgrade portworld"]
    if repo_paths is not None and _looks_like_source_checkout(repo_paths.project_root):
        commands.append("pipx install . --force")
    return "unknown", commands


def _looks_like_source_checkout(project_root: Path) -> bool:
    return (
        (project_root / "pyproject.toml").is_file()
        and (project_root / "backend" / "__init__.py").is_file()
    )


def _detect_pipx_install() -> bool:
    if shutil.which("pipx") is None:
        return False
    try:
        completed = subprocess.run(
            ["pipx", "list", "--json"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if completed.returncode != 0:
        return False
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return False
    return _payload_has_pipx_portworld(payload)


def _payload_has_pipx_portworld(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    venvs = payload.get("venvs")
    if isinstance(venvs, dict) and "portworld" in venvs:
        return True
    packages = payload.get("packages")
    if isinstance(packages, dict) and "portworld" in packages:
        return True
    if isinstance(venvs, list):
        for entry in venvs:
            if isinstance(entry, dict) and entry.get("package") == "portworld":
                return True
    return False


def _failure_result(command_name: str, exc: Exception, *, exit_code: int) -> CommandResult:
    return CommandResult(
        ok=False,
        command=command_name,
        message=str(exc),
        data={"status": "error", "error_type": type(exc).__name__},
        exit_code=exit_code,
    )
