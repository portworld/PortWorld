from __future__ import annotations

from dataclasses import dataclass
import json
import sys
from pathlib import Path
import shutil
import subprocess
from urllib.error import URLError

from portworld_cli.context import CLIContext
from portworld_cli.deploy.service import run_deploy_gcp_cloud_run
from portworld_cli.output import CommandResult, format_key_value_lines
from portworld_cli.release.identity import (
    INSTALLER_SCRIPT_URL,
    LATEST_RELEASE_API_URL,
    active_pypi_package_name,
    package_name_candidates,
)
from portworld_cli.release.lookup import (
    ReleaseLookupResult,
    compare_numeric_versions,
    extract_latest_release_tag,
    fetch_latest_release_payload,
    normalize_numeric_package_version,
)
from portworld_cli.targets import (
    TARGET_AWS_ECS_FARGATE,
    TARGET_AZURE_CONTAINER_APPS,
    TARGET_GCP_CLOUD_RUN,
)
from portworld_cli.services.cloud_contract import (
    CloudProviderOptions,
    problem_next_message,
    to_aws_deploy_options,
    to_azure_deploy_options,
    to_gcp_deploy_options,
    validate_cloud_flag_scope_for_update_deploy,
)
from portworld_cli.services.common import ErrorMappingPolicy, map_command_exception
from portworld_cli.version import __version__
from portworld_cli.workspace.discovery.paths import ProjectPaths, ProjectRootResolutionError, resolve_project_paths
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
SELF_HOST_DOCS_HINT = "See backend/README.md and portworld_cli/README.md."


class UpdateUsageError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class UpdateDeployOptions:
    cloud: CloudProviderOptions
    tag: str | None


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
    options: UpdateDeployOptions,
) -> CommandResult:
    active_target: str | None = None
    try:
        session = load_inspection_session(cli_context)
        load_workspace_session(cli_context)
        active_target = session.active_target()
        if active_target is None:
            raise UpdateUsageError(
                "No managed deploy target is configured. Use `portworld deploy <target>` first "
                "or configure managed cloud defaults with `portworld config edit cloud`."
            )
        issue = validate_cloud_flag_scope_for_update_deploy(
            active_target=active_target,
            cloud_options=options.cloud,
        )
        if issue is not None:
            return _usage_error_result(
                target=active_target,
                problem=issue.problem,
                next_step=issue.next_step,
            )

        result = _dispatch_update_deploy(cli_context, active_target=active_target, options=options)
        wrapped_message = result.message
        prefix = f"Managed redeploy target: {active_target}"
        if wrapped_message:
            wrapped_message = f"{prefix}\n\n{wrapped_message}"
        else:
            wrapped_message = prefix
        wrapped_data = dict(result.data)
        wrapped_data["target"] = active_target
        wrapped_data["wrapped_command"] = f"portworld deploy {active_target}"
        return CommandResult(
            ok=result.ok,
            command=UPDATE_DEPLOY_COMMAND_NAME,
            message=wrapped_message,
            data=wrapped_data,
            checks=result.checks,
            exit_code=result.exit_code,
        )
    except UpdateUsageError as exc:
        return _usage_error_result(
            target=active_target,
            problem=str(exc),
            next_step=(
                "Run `portworld status` to confirm the active target, then pass only that target's "
                "provider-scoped flags to `portworld update deploy`."
            ),
        )
    except Exception as exc:
        mapped = map_command_exception(
            exc,
            policy=ErrorMappingPolicy(command_name=UPDATE_DEPLOY_COMMAND_NAME),
        )
        if active_target is None:
            return mapped
        data = dict(mapped.data)
        data["target"] = active_target
        return CommandResult(
            ok=mapped.ok,
            command=mapped.command,
            message=mapped.message,
            data=data,
            checks=mapped.checks,
            exit_code=mapped.exit_code,
        )


def _dispatch_update_deploy(
    cli_context: CLIContext,
    *,
    active_target: str,
    options: UpdateDeployOptions,
) -> CommandResult:
    if active_target == TARGET_GCP_CLOUD_RUN:
        return run_deploy_gcp_cloud_run(
            cli_context,
            to_gcp_deploy_options(options.cloud, tag=options.tag),
        )
    if active_target == TARGET_AWS_ECS_FARGATE:
        from portworld_cli.aws.deploy import run_deploy_aws_ecs_fargate

        return run_deploy_aws_ecs_fargate(
            cli_context,
            to_aws_deploy_options(options.cloud, tag=options.tag),
        )
    if active_target == TARGET_AZURE_CONTAINER_APPS:
        from portworld_cli.azure.deploy import run_deploy_azure_container_apps

        return run_deploy_azure_container_apps(
            cli_context,
            to_azure_deploy_options(options.cloud, tag=options.tag),
        )
    raise UpdateUsageError(f"Managed deploy target '{active_target}' is not supported.")


def _usage_error_result(*, target: str | None, problem: str, next_step: str) -> CommandResult:
    payload = {
        "status": "error",
        "error_type": "UsageError",
    }
    if target is not None:
        payload["target"] = target
    return CommandResult(
        ok=False,
        command=UPDATE_DEPLOY_COMMAND_NAME,
        message=problem_next_message(problem=problem, next_step=next_step),
        data=payload,
        exit_code=2,
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
    normalized_target_version = normalize_numeric_package_version(release_lookup.target_version)

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
    try:
        payload = fetch_latest_release_payload(api_url=LATEST_RELEASE_API_URL)
    except (OSError, URLError, TimeoutError, json.JSONDecodeError):
        return ReleaseLookupResult(status="error", target_version=None, update_available=None)
    target_version = extract_latest_release_tag(payload)
    if target_version is None:
        return ReleaseLookupResult(status="error", target_version=None, update_available=None)
    return ReleaseLookupResult(
        status="ok",
        target_version=target_version,
        update_available=compare_numeric_versions(__version__, target_version),
    )
