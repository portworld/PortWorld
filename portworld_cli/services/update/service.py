from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
from urllib.error import URLError
from urllib.request import Request, urlopen

from backend import __version__
from portworld_cli.context import CLIContext
from portworld_cli.deploy.config import DeployGCPCloudRunOptions
from portworld_cli.deploy.service import run_deploy_gcp_cloud_run
from portworld_cli.output import CommandResult, format_key_value_lines
from portworld_cli.paths import ProjectPaths, ProjectRootResolutionError, resolve_project_paths
from portworld_cli.project_config import GCP_CLOUD_RUN_TARGET
from portworld_cli.release_identity import (
    INSTALLER_SCRIPT_URL,
    LATEST_RELEASE_API_URL,
    active_pypi_package_name,
    package_name_candidates,
)
from portworld_cli.services.common import ErrorMappingPolicy, map_command_exception
from portworld_cli.workspace.session import load_inspection_session, load_workspace_session

INSTALLER_COMMAND = f"curl -fsSL --proto '=https' --tlsv1.2 {INSTALLER_SCRIPT_URL} | bash"
INSTALLER_COMMAND_WITH_VERSION = (
    f"curl -fsSL --proto '=https' --tlsv1.2 {INSTALLER_SCRIPT_URL} | "
    "bash -s -- --version {tag}"
)
SOURCE_CHECKOUT_INSTALL_COMMAND = "pipx install . --force"
UV_TOOL_INSTALL_COMMAND = 'uv tool install "{package_name}"'
UV_TOOL_INSTALL_VERSION_COMMAND = 'uv tool install --force "{package_name}=={version}"'
UV_TOOL_UPGRADE_COMMAND = "uv tool upgrade {package_name}"
LEGACY_PIPX_UPGRADE_COMMAND = "python3 -m pipx upgrade {package_name}"
UPDATE_CLI_COMMAND_NAME = "portworld update cli"
UPDATE_DEPLOY_COMMAND_NAME = "portworld update deploy"
WRAPPED_DEPLOY_COMMAND = "portworld deploy gcp-cloud-run"
SELF_HOST_DOCS_HINT = "See backend/README.md and docs/BACKEND_SELF_HOSTING.md."


class UpdateUsageError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReleaseLookupResult:
    status: str
    target_version: str | None
    update_available: bool | None


def run_update_cli(cli_context: CLIContext) -> CommandResult:
    package_name = active_pypi_package_name()
    repo_paths = _try_resolve_repo_paths(cli_context)
    detected_mode, recommended_commands, source_checkout_root, release_lookup = _detect_cli_update_mode(repo_paths)
    message_lines = [
        format_key_value_lines(
            ("current_version", __version__),
            ("package_name", package_name),
            ("detected_install_mode", detected_mode),
            ("release_lookup_status", release_lookup.status),
        )
    ]
    effective_repo_root = (
        repo_paths.project_root
        if repo_paths is not None
        else source_checkout_root
    )
    if effective_repo_root is not None:
        message_lines.append(f"repo_root: {effective_repo_root}")
    if release_lookup.target_version is not None:
        message_lines.append(f"target_version: {release_lookup.target_version}")
    if release_lookup.update_available is not None:
        message_lines.append(
            f"update_available: {'yes' if release_lookup.update_available else 'no'}"
        )
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
            "package_name": package_name,
            "detected_install_mode": detected_mode,
            "target_version": release_lookup.target_version,
            "release_lookup_status": release_lookup.status,
            "update_available": release_lookup.update_available,
            "recommended_commands": recommended_commands,
            "repo_root": None if effective_repo_root is None else str(effective_repo_root),
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
        load_workspace_session(cli_context)
        active_target = session.active_target()
        if active_target is None:
            raise UpdateUsageError(
                "No managed deploy target is configured. Use `portworld deploy gcp-cloud-run` first "
                "or configure managed cloud defaults with `portworld config edit cloud`."
            )
        if active_target != GCP_CLOUD_RUN_TARGET:
            raise UpdateUsageError(f"Managed deploy target '{active_target}' is not supported yet.")

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
        return CommandResult(
            ok=result.ok,
            command=UPDATE_DEPLOY_COMMAND_NAME,
            message=wrapped_message,
            data=wrapped_data,
            checks=result.checks,
            exit_code=result.exit_code,
        )
    except Exception as exc:
        return map_command_exception(
            exc,
            policy=ErrorMappingPolicy(command_name=UPDATE_DEPLOY_COMMAND_NAME),
            usage_error_types=(UpdateUsageError,),
        )


def _try_resolve_repo_paths(cli_context: CLIContext) -> ProjectPaths | None:
    try:
        return resolve_project_paths(
            explicit_root=cli_context.project_root_override,
            start=Path.cwd(),
        )
    except ProjectRootResolutionError:
        return None


def _detect_cli_update_mode(
    repo_paths: ProjectPaths | None,
) -> tuple[str, list[str], Path | None, ReleaseLookupResult]:
    if repo_paths is not None and _looks_like_source_checkout(repo_paths.project_root):
        return (
            "source_checkout",
            [SOURCE_CHECKOUT_INSTALL_COMMAND],
            repo_paths.project_root,
            ReleaseLookupResult(status="skipped", target_version=None, update_available=None),
        )

    runtime_checkout_root = _resolve_runtime_source_checkout_root()
    if runtime_checkout_root is not None:
        return (
            "source_checkout",
            [SOURCE_CHECKOUT_INSTALL_COMMAND],
            runtime_checkout_root,
            ReleaseLookupResult(status="skipped", target_version=None, update_available=None),
        )

    package_name = active_pypi_package_name()
    release_lookup = _lookup_latest_release()
    normalized_target_version = _normalize_package_version(release_lookup.target_version)

    if _detect_uv_tool_runtime():
        recommended_commands = [
            UV_TOOL_UPGRADE_COMMAND.format(package_name=package_name),
            INSTALLER_COMMAND,
        ]
        if (
            release_lookup.status == "ok"
            and release_lookup.target_version is not None
            and normalized_target_version is not None
        ):
            recommended_commands = [
                UV_TOOL_UPGRADE_COMMAND.format(package_name=package_name),
                UV_TOOL_INSTALL_VERSION_COMMAND.format(
                    package_name=package_name,
                    version=normalized_target_version,
                ),
                INSTALLER_COMMAND_WITH_VERSION.format(tag=release_lookup.target_version),
            ]
        return (
            "uv_tool",
            recommended_commands,
            None,
            release_lookup,
        )

    if _detect_pipx_runtime() or _detect_pipx_install():
        recommended_commands = [
            INSTALLER_COMMAND,
            LEGACY_PIPX_UPGRADE_COMMAND.format(package_name=package_name),
        ]
        if release_lookup.status == "ok" and release_lookup.target_version is not None:
            recommended_commands.insert(
                1,
                INSTALLER_COMMAND_WITH_VERSION.format(tag=release_lookup.target_version),
            )
        return (
            "pipx_legacy",
            recommended_commands,
            None,
            release_lookup,
        )

    recommended_commands = [
        INSTALLER_COMMAND,
        UV_TOOL_INSTALL_COMMAND.format(package_name=package_name),
    ]
    if normalized_target_version is not None:
        recommended_commands.append(
            UV_TOOL_INSTALL_VERSION_COMMAND.format(
                package_name=package_name,
                version=normalized_target_version,
            )
        )
    return (
        "unknown",
        recommended_commands,
        None,
        release_lookup,
    )


def _looks_like_source_checkout(project_root: Path) -> bool:
    return (
        (project_root / "pyproject.toml").is_file()
        and (project_root / "backend" / "__init__.py").is_file()
        and (project_root / "portworld_cli" / "__init__.py").is_file()
    )


def _resolve_runtime_source_checkout_root() -> Path | None:
    current_path = Path(__file__).resolve()
    for candidate in current_path.parents:
        if _looks_like_source_checkout(candidate):
            return candidate
    return None


def _detect_uv_tool_runtime() -> bool:
    return "/uv/tools/" in Path(sys.executable).resolve().as_posix()


def _detect_pipx_runtime() -> bool:
    return "/pipx/venvs/" in Path(sys.executable).resolve().as_posix()


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
    return _payload_has_supported_pypi_package(payload)


def _payload_has_supported_pypi_package(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    candidates = package_name_candidates()
    venvs = payload.get("venvs")
    if isinstance(venvs, dict):
        for candidate in candidates:
            if candidate in venvs:
                return True
    packages = payload.get("packages")
    if isinstance(packages, dict):
        for candidate in candidates:
            if candidate in packages:
                return True
    if isinstance(venvs, list):
        for entry in venvs:
            if not isinstance(entry, dict):
                continue
            package_name = entry.get("package")
            if isinstance(package_name, str) and package_name in candidates:
                return True
    return False


def _lookup_latest_release() -> ReleaseLookupResult:
    request = Request(
        LATEST_RELEASE_API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "portworld-cli",
        },
    )
    try:
        with urlopen(request, timeout=10.0) as response:
            payload = json.load(response)
    except (OSError, URLError, TimeoutError, json.JSONDecodeError):
        return ReleaseLookupResult(status="error", target_version=None, update_available=None)
    if not isinstance(payload, dict):
        return ReleaseLookupResult(status="error", target_version=None, update_available=None)
    target_version = payload.get("tag_name")
    if not isinstance(target_version, str) or not target_version.strip():
        return ReleaseLookupResult(status="error", target_version=None, update_available=None)
    normalized_target = target_version.strip()
    return ReleaseLookupResult(
        status="ok",
        target_version=normalized_target,
        update_available=_compare_versions(__version__, normalized_target),
    )


def _compare_versions(current_version: str, target_version: str) -> bool | None:
    current_parts = _parse_version_parts(current_version)
    target_parts = _parse_version_parts(target_version)
    if current_parts is None or target_parts is None:
        return None
    return target_parts > current_parts


def _parse_version_parts(value: str) -> tuple[int, ...] | None:
    normalized = value.strip()
    if normalized.startswith("v"):
        normalized = normalized[1:]
    if not re.fullmatch(r"\d+(?:\.\d+)*", normalized):
        return None
    return tuple(int(part) for part in normalized.split("."))


def _normalize_package_version(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if normalized.startswith("v"):
        normalized = normalized[1:]
    if not re.fullmatch(r"\d+(?:\.\d+)*", normalized):
        return None
    return normalized
