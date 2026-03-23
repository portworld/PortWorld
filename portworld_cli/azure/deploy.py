from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
from pathlib import Path
import secrets
import socket
import ssl
from time import monotonic, sleep
from time import time_ns
from urllib.parse import quote, urlparse

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
        stage_records.append(_stage_ok("mutation_plan", "Confirmed deploy mutations."))

        resources.update(
            {
                "subscription_id": config.subscription_id,
                "resource_group": config.resource_group,
                "region": config.region,
                "environment_name": config.environment_name,
                "app_name": config.app_name,
                "acr_name": config.acr_name,
                "image_uri": config.image_uri,
                "storage_account": config.storage_account,
                "blob_container": config.blob_container,
                "postgres_server_name": config.postgres_server_name,
                "postgres_database_name": config.postgres_database_name,
            }
        )

        deploy_result = _run_azure_deploy_mutations(
            config=config,
            env_values=env_values,
            stage_records=stage_records,
        )
        fqdn = deploy_result.fqdn
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
                database_name=config.postgres_database_name,
                bucket_name=config.blob_container,
                image=deploy_result.image_uri,
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
                "image_uri": deploy_result.image_uri,
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
                    f"image_uri: {deploy_result.image_uri}",
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
                "runtime_env": _sanitize_runtime_env_for_output(
                    _build_runtime_env_vars(
                        env_values,
                        config,
                        database_url=deploy_result.database_url,
                    )
                ),
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
    app_name = _require_value(
        cli_context,
        value=_first_non_empty(options.app, defaults.app_name, DEFAULT_APP_NAME),
        prompt="Container App name",
        error="Container App name is required.",
    )
    resource_group = _require_value(
        cli_context,
        value=_first_non_empty(options.resource_group, defaults.resource_group, DEFAULT_RESOURCE_GROUP),
        prompt="Azure resource group",
        error="Azure resource group is required.",
    )
    region = _require_value(
        cli_context,
        value=_first_non_empty(options.region, defaults.region, DEFAULT_AZURE_REGION),
        prompt="Azure region",
        error="Azure region is required.",
    )
    environment_name = _require_value(
        cli_context,
        value=_first_non_empty(options.environment, defaults.environment_name, f"{app_name}-env"),
        prompt="Container Apps environment name",
        error="Container Apps environment name is required.",
    )
    database_url = _first_non_empty(options.database_url, env_values.get("BACKEND_DATABASE_URL"))
    if database_url and not is_postgres_url(database_url):
        raise DeployUsageError("BACKEND_DATABASE_URL must use postgres:// or postgresql://.")

    hash_seed = f"{subscription_id}:{resource_group}:{app_name}"
    unique_suffix = _stable_suffix(hash_seed, length=6)

    storage_account = _require_value(
        cli_context,
        value=_first_non_empty(options.storage_account, _build_storage_account_name(app_name, unique_suffix)),
        prompt="Azure storage account name",
        error="Azure storage account name is required.",
    )
    storage_error = validate_storage_account_name(storage_account)
    if storage_error:
        raise DeployUsageError(storage_error)

    blob_container = _require_value(
        cli_context,
        value=_first_non_empty(
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

    blob_endpoint = _require_value(
        cli_context,
        value=_first_non_empty(
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

    acr_name = _build_acr_name(app_name, unique_suffix)
    acr_server = _first_non_empty(options.acr_server, f"{acr_name}.azurecr.io")
    if acr_server is None:
        raise DeployUsageError("ACR login server could not be derived.")
    acr_repo = _require_value(
        cli_context,
        value=_first_non_empty(options.acr_repo, f"{app_name}-backend"),
        prompt="ACR repository name",
        error="ACR repository is required.",
    )
    postgres_server_name = _build_postgres_server_name(app_name, unique_suffix)
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
        acr_name=acr_name,
        acr_server=acr_server,
        acr_repo=acr_repo,
        postgres_server_name=postgres_server_name,
        postgres_database_name=postgres_database_name,
        postgres_admin_username=DEFAULT_POSTGRES_ADMIN_USERNAME,
        image_tag=image_tag,
        image_uri=image_uri,
        cors_origins=cors_origins or "*",
        allowed_hosts=allowed_hosts or "*",
        published_release_tag=published_release_tag,
        published_image_ref=published_image_ref,
    )


def _stable_suffix(seed: str, *, length: int) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return digest[:length]


def _sanitize_name_token(value: str) -> str:
    lowered = value.strip().lower()
    return "".join(char for char in lowered if char.isalnum())


def _build_storage_account_name(app_name: str, suffix: str) -> str:
    token = _sanitize_name_token(app_name) or "portworld"
    candidate = f"pw{token}{suffix}"
    return candidate[:24]


def _build_acr_name(app_name: str, suffix: str) -> str:
    token = _sanitize_name_token(app_name) or "portworld"
    candidate = f"pw{token}{suffix}"
    if len(candidate) < 5:
        candidate = f"{candidate}acr"
    return candidate[:50]


def _build_postgres_server_name(app_name: str, suffix: str) -> str:
    token = _sanitize_name_token(app_name) or "portworld"
    candidate = f"pwpg{token}{suffix}"
    if len(candidate) < 3:
        candidate = f"pwpg{suffix}"
    return candidate[:63]


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


@dataclass(frozen=True, slots=True)
class _DeployMutationResult:
    fqdn: str | None
    database_url: str
    image_uri: str


def _run_azure_deploy_mutations(
    *,
    config: _ResolvedAzureDeployConfig,
    env_values: OrderedDict[str, str],
    stage_records: list[dict[str, object]],
) -> _DeployMutationResult:
    set_subscription = run_az_json(["account", "set", "--subscription", config.subscription_id])
    if not set_subscription.ok:
        raise DeployStageError(
            stage="subscription_set",
            message=set_subscription.message or "Unable to set active Azure subscription.",
            action="Verify subscription id and az login context.",
        )
    stage_records.append(_stage_ok("subscription_set", f"Using subscription `{config.subscription_id}`."))

    _ensure_resource_group(config, stage_records)
    _ensure_resource_provider(config, stage_records, namespace="Microsoft.App")
    _ensure_resource_provider(config, stage_records, namespace="Microsoft.ContainerRegistry")
    _ensure_resource_provider(config, stage_records, namespace="Microsoft.Storage")
    _ensure_resource_provider(config, stage_records, namespace="Microsoft.DBforPostgreSQL")

    acr_server, acr_username, acr_password = _ensure_acr(config, stage_records)
    image_uri = f"{acr_server}/{config.acr_repo}:{config.image_tag}"
    _ensure_storage(config, stage_records)
    _ensure_container_apps_environment(config, stage_records)
    database_url = _ensure_postgres_and_database_url(config, stage_records)

    current_app = run_az_json(
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
    app_exists = current_app.ok and isinstance(current_app.value, dict)
    if app_exists:
        stage_records.append(_stage_ok("container_app_lookup", f"Found Container App `{config.app_name}`."))
    else:
        stage_records.append(_stage_ok("container_app_lookup", f"Container App `{config.app_name}` will be created."))

    runtime_env = _build_runtime_env_vars(env_values, config, database_url=database_url)
    plain_env, secret_env = _split_runtime_env_for_azure(runtime_env)
    env_args = [f"{key}={value}" for key, value in plain_env.items()]
    env_args.extend(f"{key}=secretref:{secret_name}" for key, secret_name in secret_env.items())
    if app_exists:
        _set_container_app_registry_credentials(
            config=config,
            acr_server=acr_server,
            acr_username=acr_username,
            acr_password=acr_password,
        )
        update_args = [
            "containerapp",
            "update",
            "--subscription",
            config.subscription_id,
            "--resource-group",
            config.resource_group,
            "--name",
            config.app_name,
            "--image",
            image_uri,
        ]
        if secret_env:
            update_args.extend(
                [
                    "--secrets",
                    *[
                        f"{secret_name}={runtime_env[key]}"
                        for key, secret_name in secret_env.items()
                    ],
                ]
            )
        if env_args:
            update_args.extend(["--set-env-vars", *env_args])
        update_response = run_az_json(update_args)
        if not update_response.ok:
            raise DeployStageError(
                stage="container_app_update",
                message=update_response.message or "Unable to update Azure Container App.",
                action="Verify app permissions and containerapp update arguments.",
            )
        stage_records.append(_stage_ok("container_app_update", f"Updated image to `{image_uri}`."))
    else:
        create_args = [
            "containerapp",
            "create",
            "--subscription",
            config.subscription_id,
            "--resource-group",
            config.resource_group,
            "--name",
            config.app_name,
            "--environment",
            config.environment_name,
            "--image",
            image_uri,
            "--ingress",
            "external",
            "--target-port",
            "8080",
            "--registry-server",
            acr_server,
            "--registry-username",
            acr_username,
            "--registry-password",
            acr_password,
        ]
        if secret_env:
            create_args.extend(
                [
                    "--secrets",
                    *[
                        f"{secret_name}={runtime_env[key]}"
                        for key, secret_name in secret_env.items()
                    ],
                ]
            )
        if env_args:
            create_args.extend(["--env-vars", *env_args])
        create_response = run_az_json(create_args)
        if not create_response.ok:
            raise DeployStageError(
                stage="container_app_create",
                message=create_response.message or "Unable to create Azure Container App.",
                action="Verify Container Apps permissions, image accessibility, and environment setup.",
            )
        stage_records.append(_stage_ok("container_app_create", f"Created Container App `{config.app_name}`."))

    fqdn = _wait_for_container_app_readiness(config=config)
    if fqdn is None:
        raise DeployStageError(
            stage="container_app_wait_ready",
            message="Container App did not report a ready external revision in time.",
            action="Inspect Container App revisions and ingress settings.",
        )
    stage_records.append(_stage_ok("container_app_wait_ready", f"Container App is ready at `{fqdn}`."))
    return _DeployMutationResult(fqdn=fqdn, database_url=database_url, image_uri=image_uri)


def _ensure_resource_group(config: _ResolvedAzureDeployConfig, stage_records: list[dict[str, object]]) -> None:
    current = run_az_json(
        [
            "group",
            "show",
            "--subscription",
            config.subscription_id,
            "--name",
            config.resource_group,
        ]
    )
    if current.ok and isinstance(current.value, dict):
        stage_records.append(_stage_ok("resource_group", f"Using resource group `{config.resource_group}`."))
        return
    created = run_az_json(
        [
            "group",
            "create",
            "--subscription",
            config.subscription_id,
            "--name",
            config.resource_group,
            "--location",
            config.region,
        ]
    )
    if not created.ok:
        raise DeployStageError(
            stage="resource_group",
            message=created.message or "Unable to create resource group.",
            action="Verify resource group permissions and target region.",
        )
    stage_records.append(_stage_ok("resource_group", f"Created resource group `{config.resource_group}`."))


def _ensure_resource_provider(
    config: _ResolvedAzureDeployConfig,
    stage_records: list[dict[str, object]],
    *,
    namespace: str,
) -> None:
    provider = run_az_json(
        [
            "provider",
            "show",
            "--subscription",
            config.subscription_id,
            "--namespace",
            namespace,
        ]
    )
    state = None
    if provider.ok and isinstance(provider.value, dict):
        state = read_dict_string(provider.value, "registrationState")
    if state == "Registered":
        stage_records.append(_stage_ok("provider_registration", f"{namespace} is registered."))
        return
    register = run_az_json(
        [
            "provider",
            "register",
            "--subscription",
            config.subscription_id,
            "--namespace",
            namespace,
        ]
    )
    if not register.ok:
        raise DeployStageError(
            stage="provider_registration",
            message=register.message or f"Unable to register Azure provider {namespace}.",
            action=f"Register provider manually: az provider register --namespace {namespace}",
        )
    stage_records.append(_stage_ok("provider_registration", f"Requested registration for {namespace}."))


def _ensure_acr(
    config: _ResolvedAzureDeployConfig,
    stage_records: list[dict[str, object]],
) -> tuple[str, str, str]:
    acr = run_az_json(
        [
            "acr",
            "show",
            "--subscription",
            config.subscription_id,
            "--resource-group",
            config.resource_group,
            "--name",
            config.acr_name,
        ]
    )
    created = False
    if not acr.ok or not isinstance(acr.value, dict):
        create = run_az_json(
            [
                "acr",
                "create",
                "--subscription",
                config.subscription_id,
                "--resource-group",
                config.resource_group,
                "--name",
                config.acr_name,
                "--location",
                config.region,
                "--sku",
                "Basic",
                "--admin-enabled",
                "true",
            ]
        )
        if not create.ok or not isinstance(create.value, dict):
            raise DeployStageError(
                stage="acr_provision",
                message=create.message or "Unable to create Azure Container Registry.",
                action="Verify ACR naming constraints and subscription permissions.",
            )
        acr = create
        created = True
    login_server = read_dict_string(acr.value, "loginServer") if isinstance(acr.value, dict) else None
    if login_server is None:
        raise DeployStageError(
            stage="acr_provision",
            message="ACR login server is missing after provisioning.",
            action="Inspect the Azure Container Registry resource in Azure CLI.",
        )
    admin_enabled = False
    if isinstance(acr.value, dict):
        admin_enabled = bool(acr.value.get("adminUserEnabled"))
    if not admin_enabled:
        update = run_az_json(
            [
                "acr",
                "update",
                "--subscription",
                config.subscription_id,
                "--resource-group",
                config.resource_group,
                "--name",
                config.acr_name,
                "--admin-enabled",
                "true",
            ]
        )
        if not update.ok:
            raise DeployStageError(
                stage="acr_provision",
                message=update.message or "Unable to enable ACR admin user.",
                action="Enable the ACR admin user and retry deploy.",
            )
    creds = run_az_json(
        [
            "acr",
            "credential",
            "show",
            "--subscription",
            config.subscription_id,
            "--resource-group",
            config.resource_group,
            "--name",
            config.acr_name,
        ]
    )
    if not creds.ok or not isinstance(creds.value, dict):
        raise DeployStageError(
            stage="acr_credentials",
            message=creds.message or "Unable to read ACR credentials.",
            action="Verify ACR admin user is enabled and credentials are accessible.",
        )
    username = read_dict_string(creds.value, "username")
    password = _extract_acr_password(creds.value)
    if username is None or password is None:
        raise DeployStageError(
            stage="acr_credentials",
            message="ACR credentials are incomplete.",
            action="Regenerate ACR credentials and retry deploy.",
        )
    message = "Created ACR" if created else "Using existing ACR"
    stage_records.append(_stage_ok("acr_provision", f"{message} `{config.acr_name}` ({login_server})."))
    return login_server, username, password


def _extract_acr_password(payload: dict[str, object]) -> str | None:
    passwords = payload.get("passwords")
    if not isinstance(passwords, list):
        return None
    for item in passwords:
        if not isinstance(item, dict):
            continue
        value = read_dict_string(item, "value")
        if value is not None:
            return value
    return None


def _ensure_storage(config: _ResolvedAzureDeployConfig, stage_records: list[dict[str, object]]) -> None:
    account = run_az_json(
        [
            "storage",
            "account",
            "show",
            "--subscription",
            config.subscription_id,
            "--resource-group",
            config.resource_group,
            "--name",
            config.storage_account,
        ]
    )
    created = False
    if not account.ok or not isinstance(account.value, dict):
        create = run_az_json(
            [
                "storage",
                "account",
                "create",
                "--subscription",
                config.subscription_id,
                "--resource-group",
                config.resource_group,
                "--name",
                config.storage_account,
                "--location",
                config.region,
                "--sku",
                "Standard_LRS",
                "--kind",
                "StorageV2",
                "--allow-blob-public-access",
                "false",
                "--min-tls-version",
                "TLS1_2",
            ]
        )
        if not create.ok:
            raise DeployStageError(
                stage="storage_provision",
                message=create.message or "Unable to create Azure Storage account.",
                action="Verify storage naming constraints and permissions.",
            )
        created = True
    keys = run_az_json(
        [
            "storage",
            "account",
            "keys",
            "list",
            "--subscription",
            config.subscription_id,
            "--resource-group",
            config.resource_group,
            "--account-name",
            config.storage_account,
        ]
    )
    account_key = _extract_storage_account_key(keys.value) if keys.ok else None
    if account_key is None:
        raise DeployStageError(
            stage="storage_provision",
            message=keys.message or "Unable to resolve storage account key.",
            action="Verify storage account key permissions.",
        )
    exists = run_az_json(
        [
            "storage",
            "container",
            "exists",
            "--name",
            config.blob_container,
            "--account-name",
            config.storage_account,
            "--account-key",
            account_key,
        ]
    )
    container_exists = False
    if exists.ok and isinstance(exists.value, dict):
        container_exists = bool(exists.value.get("exists"))
    if not container_exists:
        create_container = run_az_json(
            [
                "storage",
                "container",
                "create",
                "--name",
                config.blob_container,
                "--account-name",
                config.storage_account,
                "--account-key",
                account_key,
            ]
        )
        if not create_container.ok:
            raise DeployStageError(
                stage="storage_provision",
                message=create_container.message or "Unable to create blob container.",
                action="Verify storage credentials and container naming.",
            )
    message = "Created storage account" if created else "Using existing storage account"
    stage_records.append(_stage_ok("storage_provision", f"{message} `{config.storage_account}` and validated container `{config.blob_container}`."))


def _extract_storage_account_key(value: object) -> str | None:
    if not isinstance(value, list):
        return None
    for item in value:
        if not isinstance(item, dict):
            continue
        key = read_dict_string(item, "value")
        if key is not None:
            return key
    return None


def _ensure_container_apps_environment(
    config: _ResolvedAzureDeployConfig,
    stage_records: list[dict[str, object]],
) -> None:
    env = run_az_json(
        [
            "containerapp",
            "env",
            "show",
            "--subscription",
            config.subscription_id,
            "--resource-group",
            config.resource_group,
            "--name",
            config.environment_name,
        ]
    )
    if env.ok and isinstance(env.value, dict):
        stage_records.append(_stage_ok("container_apps_environment", f"Using environment `{config.environment_name}`."))
        return
    create = run_az_json(
        [
            "containerapp",
            "env",
            "create",
            "--subscription",
            config.subscription_id,
            "--resource-group",
            config.resource_group,
            "--name",
            config.environment_name,
            "--location",
            config.region,
        ]
    )
    if not create.ok:
        raise DeployStageError(
            stage="container_apps_environment",
            message=create.message or "Unable to create Container Apps environment.",
            action="Verify Container Apps provider registration and region availability.",
        )
    stage_records.append(_stage_ok("container_apps_environment", f"Created environment `{config.environment_name}`."))


def _ensure_postgres_and_database_url(
    config: _ResolvedAzureDeployConfig,
    stage_records: list[dict[str, object]],
) -> str:
    if config.database_url is not None:
        stage_records.append(_stage_ok("postgres_provision", "Using explicit BACKEND_DATABASE_URL value."))
        return config.database_url

    existing_secret_url = _resolve_database_url_from_container_app_secret(config)
    if existing_secret_url is not None:
        stage_records.append(_stage_ok("postgres_provision", "Using existing database URL from Container App secret."))
        return existing_secret_url

    admin_password = _generate_database_password()
    server = run_az_json(
        [
            "postgres",
            "flexible-server",
            "show",
            "--subscription",
            config.subscription_id,
            "--resource-group",
            config.resource_group,
            "--name",
            config.postgres_server_name,
        ]
    )
    fqdn: str | None = None
    if server.ok and isinstance(server.value, dict):
        fqdn = read_dict_string(server.value, "fullyQualifiedDomainName")
        update_password = run_az_json(
            [
                "postgres",
                "flexible-server",
                "update",
                "--subscription",
                config.subscription_id,
                "--resource-group",
                config.resource_group,
                "--name",
                config.postgres_server_name,
                "--admin-password",
                admin_password,
            ]
        )
        if not update_password.ok:
            raise DeployStageError(
                stage="postgres_provision",
                message=update_password.message or "Unable to rotate PostgreSQL admin password.",
                action="Provide --database-url or grant permissions to update postgres server credentials.",
            )
    else:
        create = run_az_json(
            [
                "postgres",
                "flexible-server",
                "create",
                "--subscription",
                config.subscription_id,
                "--resource-group",
                config.resource_group,
                "--name",
                config.postgres_server_name,
                "--location",
                config.region,
                "--tier",
                "Burstable",
                "--sku-name",
                "Standard_B1ms",
                "--storage-size",
                "32",
                "--version",
                "16",
                "--admin-user",
                config.postgres_admin_username,
                "--admin-password",
                admin_password,
                "--public-access",
                "0.0.0.0",
            ]
        )
        if not create.ok or not isinstance(create.value, dict):
            raise DeployStageError(
                stage="postgres_provision",
                message=create.message or "Unable to create Azure Database for PostgreSQL flexible server.",
                action="Verify postgres provider registration, region availability, and permissions.",
            )
        fqdn = read_dict_string(create.value, "fullyQualifiedDomainName")

    if fqdn is None:
        raise DeployStageError(
            stage="postgres_provision",
            message="Unable to resolve PostgreSQL server FQDN.",
            action="Inspect flexible server status and retry deploy.",
        )

    db = run_az_json(
        [
            "postgres",
            "flexible-server",
            "db",
            "show",
            "--subscription",
            config.subscription_id,
            "--resource-group",
            config.resource_group,
            "--server-name",
            config.postgres_server_name,
            "--database-name",
            config.postgres_database_name,
        ]
    )
    if not db.ok:
        create_db = run_az_json(
            [
                "postgres",
                "flexible-server",
                "db",
                "create",
                "--subscription",
                config.subscription_id,
                "--resource-group",
                config.resource_group,
                "--server-name",
                config.postgres_server_name,
                "--database-name",
                config.postgres_database_name,
            ]
        )
        if not create_db.ok:
            raise DeployStageError(
                stage="postgres_provision",
                message=create_db.message or "Unable to create PostgreSQL database.",
                action="Verify database name constraints and postgres server health.",
            )

    encoded_password = quote(admin_password, safe="")
    database_url = (
        f"postgresql://{config.postgres_admin_username}:{encoded_password}@{fqdn}:5432/"
        f"{config.postgres_database_name}?sslmode=require"
    )
    stage_records.append(
        _stage_ok(
            "postgres_provision",
            f"Provisioned PostgreSQL `{config.postgres_server_name}` and database `{config.postgres_database_name}`.",
        )
    )
    return database_url


def _resolve_database_url_from_container_app_secret(config: _ResolvedAzureDeployConfig) -> str | None:
    secret_name = _to_azure_secret_name("BACKEND_DATABASE_URL")
    secret = run_az_json(
        [
            "containerapp",
            "secret",
            "show",
            "--subscription",
            config.subscription_id,
            "--resource-group",
            config.resource_group,
            "--name",
            config.app_name,
            "--secret-name",
            secret_name,
        ]
    )
    if not secret.ok or not isinstance(secret.value, dict):
        return None
    value = read_dict_string(secret.value, "value")
    if value is None or not is_postgres_url(value):
        return None
    return value


def _set_container_app_registry_credentials(
    *,
    config: _ResolvedAzureDeployConfig,
    acr_server: str,
    acr_username: str,
    acr_password: str,
) -> None:
    response = run_az_json(
        [
            "containerapp",
            "registry",
            "set",
            "--subscription",
            config.subscription_id,
            "--resource-group",
            config.resource_group,
            "--name",
            config.app_name,
            "--server",
            acr_server,
            "--username",
            acr_username,
            "--password",
            acr_password,
        ]
    )
    if not response.ok:
        raise DeployStageError(
            stage="container_app_registry_credentials",
            message=response.message or "Unable to set Container App registry credentials.",
            action="Verify ACR permissions and retry deploy.",
        )


def _generate_database_password() -> str:
    return f"Pw-{secrets.token_urlsafe(24)}!"


def _wait_for_container_app_readiness(
    *,
    config: _ResolvedAzureDeployConfig,
    timeout_seconds: float = 300.0,
    poll_interval_seconds: float = 5.0,
) -> str | None:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        app = run_az_json(
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
        if app.ok and isinstance(app.value, dict):
            fqdn = _extract_fqdn_and_external(app.value)
            if fqdn is not None and _revision_is_ready(app.value):
                return fqdn
        sleep(poll_interval_seconds)
    return None


def _revision_is_ready(payload: dict[str, object]) -> bool:
    properties = payload.get("properties")
    if not isinstance(properties, dict):
        return False
    provisioning_state = properties.get("provisioningState")
    if isinstance(provisioning_state, str) and provisioning_state.strip() not in {"Succeeded", "Provisioned"}:
        return False
    latest_revision = properties.get("latestRevisionName")
    ready_revision = properties.get("latestReadyRevisionName")
    if isinstance(latest_revision, str) and isinstance(ready_revision, str):
        if latest_revision.strip() and ready_revision.strip() and latest_revision.strip() != ready_revision.strip():
            return False
    running_status = properties.get("runningStatus")
    if isinstance(running_status, str) and running_status.strip() and running_status.strip() != "Running":
        return False
    return True


def _extract_fqdn_and_external(payload: dict[str, object]) -> str | None:
    properties = payload.get("properties")
    if not isinstance(properties, dict):
        return None
    configuration = properties.get("configuration")
    if not isinstance(configuration, dict):
        return None
    ingress = configuration.get("ingress")
    if not isinstance(ingress, dict):
        return None
    external = ingress.get("external")
    if external is not True:
        return None
    fqdn = ingress.get("fqdn")
    if not isinstance(fqdn, str):
        return None
    normalized = fqdn.strip()
    return normalized or None


def _build_runtime_env_vars(
    env_values: OrderedDict[str, str],
    config: _ResolvedAzureDeployConfig,
    *,
    database_url: str,
) -> OrderedDict[str, str]:
    final_env: OrderedDict[str, str] = OrderedDict()
    excluded = {
        "BACKEND_DATA_DIR",
        "BACKEND_SQLITE_PATH",
        "BACKEND_STORAGE_BACKEND",
        "BACKEND_OBJECT_STORE_PROVIDER",
        "BACKEND_OBJECT_STORE_NAME",
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
    final_env["BACKEND_OBJECT_STORE_ENDPOINT"] = config.blob_endpoint
    final_env["BACKEND_OBJECT_STORE_PREFIX"] = config.app_name
    final_env["BACKEND_DATABASE_URL"] = database_url
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
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or parsed.hostname is None:
        return False
    host = parsed.hostname
    port = parsed.port or 443
    headers = {
        "Host": host,
        "Connection": "Upgrade",
        "Upgrade": "websocket",
        "Sec-WebSocket-Version": "13",
        "Sec-WebSocket-Key": "cG9ydHdvcmxkLWF6dXJlLXYxLTEyMzQ1",
    }
    token = normalize_optional_text(bearer_token)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        raw_response = _tls_http_get_upgrade(
            host=host,
            port=port,
            path="/ws/session",
            headers=headers,
            timeout=10.0,
        )
    except Exception:
        return False
    status_code = _parse_http_status_code(raw_response)
    return status_code in {101, 401}


def _tls_http_get_upgrade(
    *,
    host: str,
    port: int,
    path: str,
    headers: dict[str, str],
    timeout: float,
) -> str:
    request_lines = [f"GET {path} HTTP/1.1", *(f"{key}: {value}" for key, value in headers.items()), "", ""]
    request = "\r\n".join(request_lines).encode("ascii", errors="ignore")
    context = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=timeout) as tcp_sock:
        with context.wrap_socket(tcp_sock, server_hostname=host) as tls_sock:
            tls_sock.settimeout(timeout)
            tls_sock.sendall(request)
            chunks: list[bytes] = []
            deadline = monotonic() + timeout
            while monotonic() < deadline:
                data = tls_sock.recv(4096)
                if not data:
                    break
                chunks.append(data)
                if b"\r\n\r\n" in b"".join(chunks):
                    break
            return b"".join(chunks).decode("iso-8859-1", errors="replace")


def _parse_http_status_code(raw_response: str) -> int | None:
    status_line = raw_response.splitlines()[0] if raw_response else ""
    parts = status_line.split(" ")
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def _sanitize_runtime_env_for_output(runtime_env: OrderedDict[str, str]) -> OrderedDict[str, str]:
    redacted: OrderedDict[str, str] = OrderedDict()
    for key, value in runtime_env.items():
        upper = key.upper()
        if (
            key in {"BACKEND_DATABASE_URL", "DATABASE_URL"}
            or "TOKEN" in upper
            or "SECRET" in upper
            or "PASSWORD" in upper
            or upper.endswith("_KEY")
        ):
            redacted[key] = "***REDACTED***"
        else:
            redacted[key] = value
    return redacted


def _split_runtime_env_for_azure(runtime_env: OrderedDict[str, str]) -> tuple[OrderedDict[str, str], OrderedDict[str, str]]:
    plain_env: OrderedDict[str, str] = OrderedDict()
    secret_env: OrderedDict[str, str] = OrderedDict()
    for key, value in runtime_env.items():
        if _is_sensitive_env_key(key):
            secret_env[key] = _to_azure_secret_name(key)
        else:
            plain_env[key] = value
    return plain_env, secret_env


def _is_sensitive_env_key(key: str) -> bool:
    upper = key.upper()
    return (
        key in {"BACKEND_DATABASE_URL", "DATABASE_URL"}
        or "TOKEN" in upper
        or "SECRET" in upper
        or "PASSWORD" in upper
        or upper.endswith("_KEY")
    )


def _to_azure_secret_name(key: str) -> str:
    normalized = key.lower().replace("_", "-")
    normalized = "".join(char for char in normalized if char.isalnum() or char == "-")
    normalized = normalized.strip("-") or "secret"
    if len(normalized) > 63:
        normalized = normalized[:63].rstrip("-")
    return normalized or "secret"


def _stage_ok(stage: str, message: str) -> dict[str, object]:
    return {"stage": stage, "status": "ok", "message": message}


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
                f"acr: {config.acr_name}",
                f"storage_account: {config.storage_account}",
                f"postgres_server: {config.postgres_server_name}",
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
