from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from portworld_cli.azure.client import AzureAdapters
from portworld_cli.azure.common import (
    is_postgres_url,
    normalize_optional_text,
    read_dict_string,
    validate_blob_container_name,
    validate_blob_endpoint,
    validate_storage_account_name,
)
from portworld_cli.azure.stages.shared import (
    build_acr_name,
    build_postgres_server_name,
    build_storage_account_name,
    now_ms,
    stable_suffix,
)
from portworld_cli.context import CLIContext
from portworld_cli.deploy.config import DeployStageError, DeployUsageError
from portworld_cli.deploy.published import resolve_published_image_selection
from portworld_cli.deploy.source import resolve_source_image_tag
from portworld_cli.deploy_artifacts import IMAGE_SOURCE_MODE_SOURCE_BUILD
from portworld_cli.ux.prompts import prompt_text
from portworld_cli.workspace.project_config import RUNTIME_SOURCE_PUBLISHED

DEFAULT_AZURE_REGION = "eastus"
DEFAULT_RESOURCE_GROUP = "portworld-rg"
DEFAULT_APP_NAME = "portworld-backend"
DEFAULT_BLOB_CONTAINER = "portworld-memory"
DEFAULT_POSTGRES_DATABASE = "portworld"
DEFAULT_POSTGRES_ADMIN_USERNAME = "pwadmin"


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


@dataclass(frozen=True, slots=True)
class ResolvedAzureDeployConfig:
    runtime_source: str
    image_source_mode: str
    subscription_id: str
    tenant_id: str | None
    resource_group: str
    region: str
    environment_name: str
    app_name: str
    database_url: str | None
    storage_account: str
    blob_container: str
    blob_endpoint: str
    acr_name: str
    acr_server: str
    acr_repo: str
    postgres_server_name: str
    postgres_database_name: str
    postgres_admin_username: str
    image_tag: str
    image_uri: str
    published_release_tag: str | None
    published_image_ref: str | None


def resolve_azure_deploy_config(
    cli_context: CLIContext,
    *,
    options: DeployAzureContainerAppsOptions,
    env_values: OrderedDict[str, str],
    project_config,
    runtime_source: str,
    project_root: Path | None,
    adapters: AzureAdapters,
) -> ResolvedAzureDeployConfig:
    defaults = project_config.deploy.azure_container_apps
    account = adapters.compute.run_json(["account", "show"])
    if not account.ok or not isinstance(account.value, dict):
        raise DeployStageError(
            stage="prerequisite_validation",
            message=account.message or "Unable to read active Azure account.",
            action="Run `az login` and ensure subscription access.",
        )

    account_subscription = read_dict_string(account.value, "id")
    tenant_id = read_dict_string(account.value, "tenantId")

    subscription_id = require_value(
        cli_context,
        value=first_non_empty(options.subscription, defaults.subscription_id, account_subscription),
        prompt="Azure subscription id",
        error="Azure subscription id is required.",
    )
    app_name = require_value(
        cli_context,
        value=first_non_empty(options.app, defaults.app_name, DEFAULT_APP_NAME),
        prompt="Container App name",
        error="Container App name is required.",
    )
    resource_group = require_value(
        cli_context,
        value=first_non_empty(options.resource_group, defaults.resource_group, DEFAULT_RESOURCE_GROUP),
        prompt="Azure resource group",
        error="Azure resource group is required.",
    )
    region = require_value(
        cli_context,
        value=first_non_empty(options.region, defaults.region, DEFAULT_AZURE_REGION),
        prompt="Azure region",
        error="Azure region is required.",
    )
    environment_name = require_value(
        cli_context,
        value=first_non_empty(options.environment, defaults.environment_name, f"{app_name}-env"),
        prompt="Container Apps environment name",
        error="Container Apps environment name is required.",
    )
    database_url = first_non_empty(options.database_url, env_values.get("BACKEND_DATABASE_URL"))
    if database_url and not is_postgres_url(database_url):
        raise DeployUsageError("BACKEND_DATABASE_URL must use postgres:// or postgresql://.")

    hash_seed = f"{subscription_id}:{resource_group}:{app_name}"
    unique_suffix = stable_suffix(hash_seed, length=6)

    storage_account = require_value(
        cli_context,
        value=first_non_empty(options.storage_account, build_storage_account_name(app_name, unique_suffix)),
        prompt="Azure storage account name",
        error="Azure storage account name is required.",
    )
    storage_error = validate_storage_account_name(storage_account)
    if storage_error:
        raise DeployUsageError(storage_error)

    blob_container = require_value(
        cli_context,
        value=first_non_empty(
            options.blob_container,
            env_values.get("BACKEND_OBJECT_STORE_NAME"),
            DEFAULT_BLOB_CONTAINER,
        ),
        prompt="Azure blob container name",
        error="Azure blob container name is required.",
    )
    container_error = validate_blob_container_name(blob_container)
    if container_error:
        raise DeployUsageError(container_error)

    blob_endpoint = require_value(
        cli_context,
        value=first_non_empty(
            options.blob_endpoint,
            env_values.get("BACKEND_OBJECT_STORE_ENDPOINT"),
            f"https://{storage_account}.blob.core.windows.net",
        ),
        prompt="Azure blob endpoint URL",
        error="Azure blob endpoint is required.",
    )
    endpoint_error = validate_blob_endpoint(blob_endpoint)
    if endpoint_error:
        raise DeployUsageError(endpoint_error)

    acr_name = build_acr_name(app_name, unique_suffix)
    acr_server = first_non_empty(options.acr_server, f"{acr_name}.azurecr.io")
    if acr_server is None:
        raise DeployUsageError("ACR login server could not be derived.")
    acr_repo = require_value(
        cli_context,
        value=first_non_empty(options.acr_repo, f"{app_name}-backend"),
        prompt="ACR repository name",
        error="ACR repository is required.",
    )
    postgres_server_name = build_postgres_server_name(app_name, unique_suffix)
    postgres_database_name = DEFAULT_POSTGRES_DATABASE

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
            image_tag = normalize_optional_text(options.tag) or str(now_ms())
        else:
            image_tag = resolve_source_image_tag(explicit_tag=options.tag, project_root=project_root)

    image_uri = f"{acr_server}/{acr_repo}:{image_tag}"

    return ResolvedAzureDeployConfig(
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
        acr_name=acr_name,
        acr_server=acr_server,
        acr_repo=acr_repo,
        postgres_server_name=postgres_server_name,
        postgres_database_name=postgres_database_name,
        postgres_admin_username=DEFAULT_POSTGRES_ADMIN_USERNAME,
        image_tag=image_tag,
        image_uri=image_uri,
        published_release_tag=published_release_tag,
        published_image_ref=published_image_ref,
    )


def require_value(cli_context: CLIContext, *, value: str | None, prompt: str, error: str) -> str:
    normalized = normalize_optional_text(value)
    if normalized is not None:
        return normalized
    if cli_context.non_interactive:
        raise DeployUsageError(error)
    prompted = prompt_text(
        cli_context,
        message=prompt,
        default="",
        show_default=False,
    )
    normalized = normalize_optional_text(prompted)
    if normalized is None:
        raise DeployUsageError(error)
    return normalized


def first_non_empty(*values: str | None) -> str | None:
    for value in values:
        normalized = normalize_optional_text(value)
        if normalized is not None:
            return normalized
    return None
