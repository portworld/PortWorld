from __future__ import annotations

from dataclasses import dataclass
import json
import sys
from pathlib import Path
import shutil
import subprocess
from urllib.error import URLError

from backend import __version__
from portworld_cli.aws.deploy import DeployAWSECSFargateOptions, run_deploy_aws_ecs_fargate
from portworld_cli.azure.deploy import (
    DeployAzureContainerAppsOptions,
    run_deploy_azure_container_apps,
)
from portworld_cli.context import CLIContext
from portworld_cli.deploy.config import DeployGCPCloudRunOptions
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
from portworld_cli.services.common import ErrorMappingPolicy, map_command_exception
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
SELF_HOST_DOCS_HINT = "See backend/README.md and docs/operations/BACKEND_SELF_HOSTING.md."


class UpdateUsageError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class UpdateDeployOptions:
    project: str | None
    region: str | None
    service: str | None
    artifact_repo: str | None
    sql_instance: str | None
    database: str | None
    bucket: str | None
    cors_origins: str | None
    allowed_hosts: str | None
    tag: str | None
    min_instances: int | None
    max_instances: int | None
    concurrency: int | None
    cpu: str | None
    memory: str | None
    aws_region: str | None
    aws_service: str | None
    aws_cluster: str | None
    aws_vpc_id: str | None
    aws_subnet_ids: str | None
    aws_database_url: str | None
    aws_s3_bucket: str | None
    aws_ecr_repo: str | None
    azure_subscription: str | None
    azure_resource_group: str | None
    azure_region: str | None
    azure_environment: str | None
    azure_app: str | None
    azure_database_url: str | None
    azure_storage_account: str | None
    azure_blob_container: str | None
    azure_blob_endpoint: str | None
    azure_acr_server: str | None
    azure_acr_repo: str | None


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
    try:
        session = load_inspection_session(cli_context)
        load_workspace_session(cli_context)
        active_target = session.active_target()
        if active_target is None:
            raise UpdateUsageError(
                "No managed deploy target is configured. Use `portworld deploy <target>` first "
                "or configure managed cloud defaults with `portworld config edit cloud`."
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
    except Exception as exc:
        return map_command_exception(
            exc,
            policy=ErrorMappingPolicy(command_name=UPDATE_DEPLOY_COMMAND_NAME),
            usage_error_types=(UpdateUsageError,),
        )


def _dispatch_update_deploy(
    cli_context: CLIContext,
    *,
    active_target: str,
    options: UpdateDeployOptions,
) -> CommandResult:
    if active_target == TARGET_GCP_CLOUD_RUN:
        _ensure_gcp_only_flags(options)
        return run_deploy_gcp_cloud_run(
            cli_context,
            DeployGCPCloudRunOptions(
                project=options.project,
                region=options.region,
                service=options.service,
                artifact_repo=options.artifact_repo,
                sql_instance=options.sql_instance,
                database=options.database,
                bucket=options.bucket,
                cors_origins=options.cors_origins,
                allowed_hosts=options.allowed_hosts,
                tag=options.tag,
                min_instances=options.min_instances,
                max_instances=options.max_instances,
                concurrency=options.concurrency,
                cpu=options.cpu,
                memory=options.memory,
            ),
        )
    if active_target == TARGET_AWS_ECS_FARGATE:
        _ensure_aws_only_flags(options)
        return run_deploy_aws_ecs_fargate(
            cli_context,
            DeployAWSECSFargateOptions(
                region=options.aws_region,
                cluster=options.aws_cluster,
                service=options.aws_service,
                vpc_id=options.aws_vpc_id,
                subnet_ids=options.aws_subnet_ids,
                database_url=options.aws_database_url,
                bucket=options.aws_s3_bucket,
                ecr_repo=options.aws_ecr_repo,
                tag=options.tag,
                cors_origins=options.cors_origins,
                allowed_hosts=options.allowed_hosts,
            ),
        )
    if active_target == TARGET_AZURE_CONTAINER_APPS:
        _ensure_azure_only_flags(options)
        return run_deploy_azure_container_apps(
            cli_context,
            DeployAzureContainerAppsOptions(
                subscription=options.azure_subscription,
                resource_group=options.azure_resource_group,
                region=options.azure_region,
                environment=options.azure_environment,
                app=options.azure_app,
                database_url=options.azure_database_url,
                storage_account=options.azure_storage_account,
                blob_container=options.azure_blob_container,
                blob_endpoint=options.azure_blob_endpoint,
                acr_server=options.azure_acr_server,
                acr_repo=options.azure_acr_repo,
                tag=options.tag,
                cors_origins=options.cors_origins,
                allowed_hosts=options.allowed_hosts,
            ),
        )
    raise UpdateUsageError(f"Managed deploy target '{active_target}' is not supported.")


def _ensure_gcp_only_flags(options: UpdateDeployOptions) -> None:
    if _has_aws_flags(options) or _has_azure_flags(options):
        raise UpdateUsageError(
            "AWS/Azure flags are not supported when the active managed target is gcp-cloud-run."
        )


def _ensure_aws_only_flags(options: UpdateDeployOptions) -> None:
    if _has_gcp_flags(options) or _has_azure_flags(options):
        raise UpdateUsageError(
            "GCP/Azure flags are not supported when the active managed target is aws-ecs-fargate."
        )


def _ensure_azure_only_flags(options: UpdateDeployOptions) -> None:
    if _has_gcp_flags(options) or _has_aws_flags(options):
        raise UpdateUsageError(
            "GCP/AWS flags are not supported when the active managed target is azure-container-apps."
        )


def _has_gcp_flags(options: UpdateDeployOptions) -> bool:
    return any(
        value is not None
        for value in (
            options.project,
            options.region,
            options.service,
            options.artifact_repo,
            options.sql_instance,
            options.database,
            options.bucket,
            options.min_instances,
            options.max_instances,
            options.concurrency,
            options.cpu,
            options.memory,
        )
    )


def _has_aws_flags(options: UpdateDeployOptions) -> bool:
    return any(
        value is not None
        for value in (
            options.aws_region,
            options.aws_service,
            options.aws_cluster,
            options.aws_vpc_id,
            options.aws_subnet_ids,
            options.aws_database_url,
            options.aws_s3_bucket,
            options.aws_ecr_repo,
        )
    )


def _has_azure_flags(options: UpdateDeployOptions) -> bool:
    return any(
        value is not None
        for value in (
            options.azure_subscription,
            options.azure_resource_group,
            options.azure_region,
            options.azure_environment,
            options.azure_app,
            options.azure_database_url,
            options.azure_storage_account,
            options.azure_blob_container,
            options.azure_blob_endpoint,
            options.azure_acr_server,
            options.azure_acr_repo,
        )
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
