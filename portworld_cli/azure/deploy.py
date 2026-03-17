from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from time import time_ns

import click
import httpx

from portworld_cli.azure.common import (
    azure_cli_available,
    is_postgres_url,
    normalize_optional_text,
    read_dict_string,
    run_az_json,
    validate_blob_container_name,
    validate_blob_endpoint,
    validate_storage_account_name,
)
from portworld_cli.context import CLIContext
from portworld_cli.deploy.config import DeployStageError, DeployUsageError, load_deploy_session
from portworld_cli.deploy.published import resolve_published_image_selection
from portworld_cli.deploy.source import resolve_source_image_tag
from portworld_cli.deploy_artifacts import IMAGE_SOURCE_MODE_SOURCE_BUILD
from portworld_cli.deploy_state import DeployState, write_deploy_state
from portworld_cli.output import CommandResult
from portworld_cli.targets import TARGET_AZURE_CONTAINER_APPS
from portworld_cli.workspace.project_config import RUNTIME_SOURCE_PUBLISHED

COMMAND_NAME = "portworld deploy azure-container-apps"


@dataclass(frozen=True, slots=True)
class DeployAzureContainerAppsOptions:
    subscription: str | None
    resource_group: str | None
    region: str | None
    environment: str | None
    app: str | None
    database_url: str | None
    storage_account: str | None
    blob_container: str | None
    blob_endpoint: str | None
    acr_server: str | None
    acr_repo: str | None
    tag: str | None
    cors_origins: str | None
    allowed_hosts: str | None


@dataclass(frozen=True, slots=True)
class _ResolvedAzureDeployConfig:
    runtime_source: str
    image_source_mode: str
    subscription_id: str
    tenant_id: str | None
    resource_group: str
    region: str
    environment_name: str
    app_name: str
    database_url: str
    storage_account: str
    blob_container: str
    blob_endpoint: str
    acr_server: str
    acr_repo: str
    image_tag: str
    image_uri: str
    cors_origins: str
    allowed_hosts: str
    published_release_tag: str | None
    published_image_ref: str | None


def run_deploy_azure_container_apps(
    cli_context: CLIContext,
    options: DeployAzureContainerAppsOptions,
) -> CommandResult:
    resources: dict[str, object] = {}
    stage_records: list[dict[str, object]] = []
    try:
        session = load_deploy_session(cli_context)
        if not azure_cli_available():
            raise DeployStageError(
                stage="prerequisite_validation",
                message="Azure CLI is not installed or not on PATH.",
                action="Install Azure CLI and retry deploy.",
            )

        env_values = OrderedDict(session.merged_env_values().items())
        config = _resolve_azure_deploy_config(
            cli_context,
            options=options,
            env_values=env_values,
            project_config=session.project_config,
            runtime_source=session.effective_runtime_source,
            project_root=(None if session.project_paths is None else session.project_paths.project_root),
        )

        _confirm_mutations(cli_context, config)

        fqdn = _resolve_container_app_fqdn(config)
        if fqdn is None:
            raise DeployStageError(
                stage="post_deploy_validation",
                message="Container Apps ingress FQDN was not found.",
                action="Ensure the app exists with external ingress enabled.",
            )
        service_url = f"https://{fqdn}"

        livez_ok = _probe_livez(service_url)
        ws_ok = _probe_ws(service_url, env_values.get("BACKEND_BEARER_TOKEN", ""))
        if not livez_ok:
            raise DeployStageError(
                stage="post_deploy_validation",
                message="Container Apps endpoint did not return 200 from /livez.",
                action="Verify app revision health and ingress configuration.",
            )
        if not ws_ok:
            raise DeployStageError(
                stage="post_deploy_validation",
                message="Container Apps endpoint did not complete /ws/session websocket handshake.",
                action="Verify ingress websocket behavior and Authorization header handling.",
            )

        write_deploy_state(
            session.workspace_paths.state_file_for_target(TARGET_AZURE_CONTAINER_APPS),
            DeployState(
                project_id=config.subscription_id,
                region=config.region,
                service_name=config.app_name,
                runtime_source=config.runtime_source,
                image_source_mode=config.image_source_mode,
                artifact_repository=config.acr_repo,
                artifact_repository_base=config.acr_repo,
                cloud_sql_instance=None,
                database_name="external",
                bucket_name=config.blob_container,
                image=config.image_uri,
                published_release_tag=config.published_release_tag,
                published_image_ref=config.published_image_ref,
                service_url=service_url,
                service_account_email=None,
                last_deployed_at_ms=_now_ms(),
            ),
        )

        resources.update(
            {
                "subscription_id": config.subscription_id,
                "resource_group": config.resource_group,
                "region": config.region,
                "environment_name": config.environment_name,
                "app_name": config.app_name,
                "service_url": service_url,
                "image_uri": config.image_uri,
                "blob_container": config.blob_container,
            }
        )

        return CommandResult(
            ok=True,
            command=COMMAND_NAME,
            message="\n".join(
                [
                    f"target: {TARGET_AZURE_CONTAINER_APPS}",
                    f"subscription_id: {config.subscription_id}",
                    f"resource_group: {config.resource_group}",
                    f"region: {config.region}",
                    f"environment_name: {config.environment_name}",
                    f"app_name: {config.app_name}",
                    f"service_url: {service_url}",
                    f"image_source_mode: {config.image_source_mode}",
                    f"image_uri: {config.image_uri}",
                    "next_steps:",
                    f"- curl {service_url.rstrip('/')}/livez",
                    f"- portworld doctor --target azure-container-apps --azure-subscription {config.subscription_id}",
                ]
            ),
            data={
                "target": TARGET_AZURE_CONTAINER_APPS,
                "service_url": service_url,
                "runtime_source": config.runtime_source,
                "image_source_mode": config.image_source_mode,
                "published_release_tag": config.published_release_tag,
                "published_image_ref": config.published_image_ref,
                "resources": resources,
                "stages": stage_records,
                "runtime_env": _build_runtime_env_vars(env_values, config),
            },
            exit_code=0,
        )
    except DeployUsageError as exc:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message=str(exc),
            data={"error_type": type(exc).__name__, "resources": resources, "stages": stage_records},
            exit_code=2,
        )
    except DeployStageError as exc:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message=f"stage: {exc.stage}\nerror: {exc}",
            data={"stage": exc.stage, "error_type": type(exc).__name__, "resources": resources, "stages": stage_records},
            exit_code=1,
        )
    except click.Abort:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message="Aborted before deploy completed.",
            data={"error_type": "Abort", "resources": resources, "stages": stage_records},
            exit_code=1,
        )


def _resolve_azure_deploy_config(
    cli_context: CLIContext,
    *,
    options: DeployAzureContainerAppsOptions,
    env_values: OrderedDict[str, str],
    project_config,
    runtime_source: str,
    project_root: Path | None,
) -> _ResolvedAzureDeployConfig:
    defaults = project_config.deploy.azure_container_apps
    account = run_az_json(["account", "show"])
    if not account.ok or not isinstance(account.value, dict):
        raise DeployStageError(
            stage="prerequisite_validation",
            message=account.message or "Unable to read active Azure account.",
            action="Run `az login` and ensure subscription access.",
        )

    account_subscription = read_dict_string(account.value, "id")
    tenant_id = read_dict_string(account.value, "tenantId")

    subscription_id = _require_value(
        cli_context,
        value=_first_non_empty(options.subscription, defaults.subscription_id, account_subscription),
        prompt="Azure subscription id",
        error="Azure subscription id is required.",
    )
    resource_group = _require_value(
        cli_context,
        value=_first_non_empty(options.resource_group, defaults.resource_group),
        prompt="Azure resource group",
        error="Azure resource group is required.",
    )
    region = _require_value(
        cli_context,
        value=_first_non_empty(options.region, defaults.region),
        prompt="Azure region",
        error="Azure region is required.",
    )
    environment_name = _require_value(
        cli_context,
        value=_first_non_empty(options.environment, defaults.environment_name),
        prompt="Container Apps environment name",
        error="Container Apps environment name is required.",
    )
    app_name = _require_value(
        cli_context,
        value=_first_non_empty(options.app, defaults.app_name),
        prompt="Container App name",
        error="Container App name is required.",
    )

    database_url = _require_value(
        cli_context,
        value=_first_non_empty(options.database_url, env_values.get("BACKEND_DATABASE_URL")),
        prompt="Managed PostgreSQL URL",
        error="BACKEND_DATABASE_URL is required.",
    )
    if not is_postgres_url(database_url):
        raise DeployUsageError("BACKEND_DATABASE_URL must use postgres:// or postgresql://.")

    storage_account = _require_value(
        cli_context,
        value=options.storage_account,
        prompt="Azure storage account name",
        error="Azure storage account name is required.",
    )
    storage_error = validate_storage_account_name(storage_account)
    if storage_error:
        raise DeployUsageError(storage_error)

    blob_container = _require_value(
        cli_context,
        value=_first_non_empty(options.blob_container, env_values.get("BACKEND_OBJECT_STORE_NAME"), env_values.get("BACKEND_OBJECT_STORE_BUCKET")),
        prompt="Azure blob container name",
        error="Azure blob container name is required.",
    )
    container_error = validate_blob_container_name(blob_container)
    if container_error:
        raise DeployUsageError(container_error)

    blob_endpoint = _require_value(
        cli_context,
        value=_first_non_empty(options.blob_endpoint, env_values.get("BACKEND_OBJECT_STORE_ENDPOINT")),
        prompt="Azure blob endpoint URL",
        error="Azure blob endpoint is required.",
    )
    endpoint_error = validate_blob_endpoint(blob_endpoint)
    if endpoint_error:
        raise DeployUsageError(endpoint_error)

    acr_server = _require_value(
        cli_context,
        value=options.acr_server,
        prompt="ACR login server",
        error="ACR login server is required.",
    )
    acr_repo = _require_value(
        cli_context,
        value=_first_non_empty(options.acr_repo, f"{app_name}-backend"),
        prompt="ACR repository name",
        error="ACR repository is required.",
    )

    image_source_mode = IMAGE_SOURCE_MODE_SOURCE_BUILD
    published_release_tag: str | None = None
    published_image_ref: str | None = None
    if runtime_source == RUNTIME_SOURCE_PUBLISHED:
        published = resolve_published_image_selection(
            explicit_tag=options.tag,
            artifact_repository=acr_repo,
            release_tag=project_config.deploy.published_runtime.release_tag,
            image_ref=project_config.deploy.published_runtime.image_ref,
        )
        image_source_mode = published.image_source_mode
        image_tag = published.image_tag
        published_release_tag = published.release_tag
        published_image_ref = published.image_ref
    else:
        if project_root is None:
            image_tag = normalize_optional_text(options.tag) or str(_now_ms())
        else:
            image_tag = resolve_source_image_tag(explicit_tag=options.tag, project_root=project_root)

    image_uri = f"{acr_server}/{acr_repo}:{image_tag}"

    cors_origins = _first_non_empty(options.cors_origins, env_values.get("CORS_ORIGINS"), "*")
    allowed_hosts = _first_non_empty(options.allowed_hosts, env_values.get("BACKEND_ALLOWED_HOSTS"), "*")

    return _ResolvedAzureDeployConfig(
        runtime_source=runtime_source,
        image_source_mode=image_source_mode,
        subscription_id=subscription_id,
        tenant_id=tenant_id,
        resource_group=resource_group,
        region=region,
        environment_name=environment_name,
        app_name=app_name,
        database_url=database_url,
        storage_account=storage_account,
        blob_container=blob_container,
        blob_endpoint=blob_endpoint,
        acr_server=acr_server,
        acr_repo=acr_repo,
        image_tag=image_tag,
        image_uri=image_uri,
        cors_origins=cors_origins or "*",
        allowed_hosts=allowed_hosts or "*",
        published_release_tag=published_release_tag,
        published_image_ref=published_image_ref,
    )


def _resolve_container_app_fqdn(config: _ResolvedAzureDeployConfig) -> str | None:
    response = run_az_json(
        [
            "containerapp",
            "show",
            "--subscription",
            config.subscription_id,
            "--resource-group",
            config.resource_group,
            "--name",
            config.app_name,
        ]
    )
    if not response.ok or not isinstance(response.value, dict):
        return None

    properties = response.value.get("properties")
    if not isinstance(properties, dict):
        return None
    configuration = properties.get("configuration")
    if not isinstance(configuration, dict):
        return None
    ingress = configuration.get("ingress")
    if not isinstance(ingress, dict):
        return None
    fqdn = ingress.get("fqdn")
    if not isinstance(fqdn, str):
        return None
    normalized = fqdn.strip()
    return normalized or None


def _build_runtime_env_vars(
    env_values: OrderedDict[str, str],
    config: _ResolvedAzureDeployConfig,
) -> OrderedDict[str, str]:
    final_env: OrderedDict[str, str] = OrderedDict()
    excluded = {
        "BACKEND_DATA_DIR",
        "BACKEND_SQLITE_PATH",
        "BACKEND_STORAGE_BACKEND",
        "BACKEND_OBJECT_STORE_PROVIDER",
        "BACKEND_OBJECT_STORE_NAME",
        "BACKEND_OBJECT_STORE_BUCKET",
        "BACKEND_OBJECT_STORE_ENDPOINT",
        "BACKEND_OBJECT_STORE_PREFIX",
        "BACKEND_DATABASE_URL",
        "PORT",
    }
    for key, value in env_values.items():
        if key in excluded:
            continue
        final_env[key] = value

    final_env["BACKEND_PROFILE"] = "production"
    final_env["BACKEND_STORAGE_BACKEND"] = "managed"
    final_env["BACKEND_OBJECT_STORE_PROVIDER"] = "azure_blob"
    final_env["BACKEND_OBJECT_STORE_NAME"] = config.blob_container
    final_env["BACKEND_OBJECT_STORE_BUCKET"] = config.blob_container
    final_env["BACKEND_OBJECT_STORE_ENDPOINT"] = config.blob_endpoint
    final_env["BACKEND_OBJECT_STORE_PREFIX"] = config.app_name
    final_env["BACKEND_DATABASE_URL"] = config.database_url
    final_env["CORS_ORIGINS"] = config.cors_origins
    final_env["BACKEND_ALLOWED_HOSTS"] = config.allowed_hosts
    return final_env


def _probe_livez(base_url: str) -> bool:
    try:
        response = httpx.get(f"{base_url.rstrip('/')}/livez", timeout=10.0)
    except Exception:
        return False
    return response.status_code == 200


def _probe_ws(base_url: str, bearer_token: str | None) -> bool:
    headers = {
        "Connection": "Upgrade",
        "Upgrade": "websocket",
        "Sec-WebSocket-Version": "13",
        "Sec-WebSocket-Key": "cG9ydHdvcmxkLWF6dXJlLXYxLTEyMzQ1",
    }
    token = normalize_optional_text(bearer_token)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = httpx.get(f"{base_url.rstrip('/')}/ws/session", headers=headers, timeout=10.0)
    except Exception:
        return False
    return response.status_code in {101, 400, 401, 426}


def _confirm_mutations(cli_context: CLIContext, config: _ResolvedAzureDeployConfig) -> None:
    if cli_context.non_interactive or cli_context.yes:
        return
    confirmed = click.confirm(
        "\n".join(
            [
                "Proceed with Azure Container Apps deploy recording and validation?",
                f"subscription_id: {config.subscription_id}",
                f"resource_group: {config.resource_group}",
                f"environment: {config.environment_name}",
                f"app: {config.app_name}",
                f"image_uri: {config.image_uri}",
            ]
        ),
        default=True,
        show_default=True,
    )
    if not confirmed:
        raise click.Abort()


def _require_value(cli_context: CLIContext, *, value: str | None, prompt: str, error: str) -> str:
    normalized = normalize_optional_text(value)
    if normalized is not None:
        return normalized
    if cli_context.non_interactive:
        raise DeployUsageError(error)
    prompted = click.prompt(prompt, default="", show_default=False)
    normalized = normalize_optional_text(prompted)
    if normalized is None:
        raise DeployUsageError(error)
    return normalized


def _first_non_empty(*values: str | None) -> str | None:
    for value in values:
        normalized = normalize_optional_text(value)
        if normalized is not None:
            return normalized
    return None


def _now_ms() -> int:
    return time_ns() // 1_000_000
