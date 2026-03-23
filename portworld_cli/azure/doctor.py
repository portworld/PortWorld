from __future__ import annotations

import hashlib
from dataclasses import dataclass

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
from portworld_cli.output import DiagnosticCheck
from portworld_cli.workspace.project_config import ProjectConfig

DEFAULT_AZURE_REGION = "eastus"
DEFAULT_RESOURCE_GROUP = "portworld-rg"
DEFAULT_APP_NAME = "portworld-backend"
DEFAULT_BLOB_CONTAINER = "portworld-memory"
DEFAULT_POSTGRES_DATABASE = "portworld"


@dataclass(frozen=True, slots=True)
class AzureDoctorDetails:
    subscription_id: str | None
    tenant_id: str | None
    region: str | None
    resource_group: str | None
    environment_name: str | None
    app_name: str | None
    fqdn: str | None
    storage_account: str | None
    blob_container: str | None
    blob_endpoint: str | None
    acr_name: str | None
    acr_server: str | None
    postgres_server_name: str | None
    postgres_database_name: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "subscription_id": self.subscription_id,
            "tenant_id": self.tenant_id,
            "region": self.region,
            "resource_group": self.resource_group,
            "environment_name": self.environment_name,
            "app_name": self.app_name,
            "fqdn": self.fqdn,
            "storage_account": self.storage_account,
            "blob_container": self.blob_container,
            "blob_endpoint": self.blob_endpoint,
            "acr_name": self.acr_name,
            "acr_server": self.acr_server,
            "postgres_server_name": self.postgres_server_name,
            "postgres_database_name": self.postgres_database_name,
        }


@dataclass(frozen=True, slots=True)
class AzureDoctorEvaluation:
    ok: bool
    checks: tuple[DiagnosticCheck, ...]
    details: AzureDoctorDetails


def evaluate_azure_container_apps_readiness(
    *,
    explicit_subscription: str | None,
    explicit_resource_group: str | None,
    explicit_region: str | None,
    explicit_environment: str | None,
    explicit_app: str | None,
    explicit_database_url: str | None,
    explicit_storage_account: str | None,
    explicit_blob_container: str | None,
    explicit_blob_endpoint: str | None,
    env_values: dict[str, str],
    project_config: ProjectConfig | None,
) -> AzureDoctorEvaluation:
    checks: list[DiagnosticCheck] = []
    azure_defaults = None if project_config is None else project_config.deploy.azure_container_apps

    subscription_id = _first_non_empty(
        explicit_subscription,
        None if azure_defaults is None else azure_defaults.subscription_id,
    )
    app_name = _first_non_empty(
        explicit_app,
        None if azure_defaults is None else azure_defaults.app_name,
        DEFAULT_APP_NAME,
    )
    resource_group = _first_non_empty(
        explicit_resource_group,
        None if azure_defaults is None else azure_defaults.resource_group,
        DEFAULT_RESOURCE_GROUP,
    )
    region = _first_non_empty(
        explicit_region,
        None if azure_defaults is None else azure_defaults.region,
        DEFAULT_AZURE_REGION,
    )
    environment_name = _first_non_empty(
        explicit_environment,
        None if azure_defaults is None else azure_defaults.environment_name,
        None if app_name is None else f"{app_name}-env",
    )
    database_url = _first_non_empty(explicit_database_url, env_values.get("BACKEND_DATABASE_URL"))

    seed = f"{subscription_id or 'unknown'}:{resource_group or 'unknown'}:{app_name or 'unknown'}"
    unique_suffix = _stable_suffix(seed, length=6)
    storage_account = _first_non_empty(
        explicit_storage_account,
        None if app_name is None else _build_storage_account_name(app_name, unique_suffix),
    )
    blob_container = _first_non_empty(
        explicit_blob_container,
        env_values.get("BACKEND_OBJECT_STORE_NAME"),
        DEFAULT_BLOB_CONTAINER,
    )
    blob_endpoint = _first_non_empty(
        explicit_blob_endpoint,
        env_values.get("BACKEND_OBJECT_STORE_ENDPOINT"),
        None if storage_account is None else f"https://{storage_account}.blob.core.windows.net",
    )
    acr_name = None if app_name is None else _build_acr_name(app_name, unique_suffix)
    acr_server = None if acr_name is None else f"{acr_name}.azurecr.io"
    postgres_server_name = None if app_name is None else _build_postgres_server_name(app_name, unique_suffix)
    postgres_database_name = DEFAULT_POSTGRES_DATABASE

    cli_ok = azure_cli_available()
    checks.append(
        DiagnosticCheck(
            id="az_cli_installed",
            status="pass" if cli_ok else "fail",
            message="Azure CLI is installed" if cli_ok else "Azure CLI is not installed or not on PATH.",
            action=None if cli_ok else "Install Azure CLI and retry doctor.",
        )
    )

    tenant_id: str | None = None
    if cli_ok:
        extension = run_az_json(["extension", "show", "--name", "containerapp"])
        checks.append(
            DiagnosticCheck(
                id="az_containerapp_extension_ready",
                status="pass" if extension.ok else "fail",
                message=(
                    "Azure `containerapp` extension is available."
                    if extension.ok
                    else (extension.message or "Azure `containerapp` extension is missing.")
                ),
                action=(
                    None
                    if extension.ok
                    else "Install or update the extension: `az extension add --name containerapp --upgrade`."
                ),
            )
        )

        account = run_az_json(["account", "show"])
        if account.ok and isinstance(account.value, dict):
            if subscription_id is None:
                subscription_id = read_dict_string(account.value, "id")
            tenant_id = read_dict_string(account.value, "tenantId")
            checks.append(
                DiagnosticCheck(
                    id="az_authenticated",
                    status="pass",
                    message="Azure account context is available.",
                )
            )
        else:
            checks.append(
                DiagnosticCheck(
                    id="az_authenticated",
                    status="fail",
                    message=account.message or "Unable to read Azure account context.",
                    action="Run `az login` and ensure subscription access.",
                )
            )

    checks.extend(
        [
            _required_check("azure_subscription_selected", subscription_id, "Provide --azure-subscription or set active az account."),
            _required_check("azure_resource_group_selected", resource_group, "--azure-resource-group is required."),
            _required_check("azure_region_selected", region, "--azure-region is required."),
            _required_check("azure_environment_selected", environment_name, "--azure-environment is required."),
            _required_check("azure_app_selected", app_name, "--azure-app is required."),
        ]
    )

    if storage_account is None:
        checks.append(
            DiagnosticCheck(
                id="storage_account_name_valid",
                status="fail",
                message="Azure storage account name is missing.",
                action="Pass --azure-storage-account.",
            )
        )
    else:
        account_name_error = validate_storage_account_name(storage_account)
        checks.append(
            DiagnosticCheck(
                id="storage_account_name_valid",
                status="pass" if account_name_error is None else "fail",
                message=(
                    f"Storage account name '{storage_account}' is valid."
                    if account_name_error is None
                    else account_name_error
                ),
                action=None if account_name_error is None else "Use a valid Azure storage account name.",
            )
        )

    if blob_container is None:
        checks.append(
            DiagnosticCheck(
                id="blob_container_name_valid",
                status="fail",
                message="Azure blob container name is missing.",
                action="Pass --azure-blob-container or set BACKEND_OBJECT_STORE_NAME.",
            )
        )
    else:
        container_error = validate_blob_container_name(blob_container)
        checks.append(
            DiagnosticCheck(
                id="blob_container_name_valid",
                status="pass" if container_error is None else "fail",
                message=(
                    f"Blob container name '{blob_container}' is valid."
                    if container_error is None
                    else container_error
                ),
                action=None if container_error is None else "Use a valid blob container name.",
            )
        )

    if blob_endpoint is None:
        checks.append(
            DiagnosticCheck(
                id="blob_endpoint_valid",
                status="fail",
                message="Azure blob endpoint is missing.",
                action="Pass --azure-blob-endpoint or set BACKEND_OBJECT_STORE_ENDPOINT.",
            )
        )
    else:
        endpoint_error = validate_blob_endpoint(blob_endpoint)
        checks.append(
            DiagnosticCheck(
                id="blob_endpoint_valid",
                status="pass" if endpoint_error is None else "fail",
                message=(
                    f"Blob endpoint '{blob_endpoint}' is valid."
                    if endpoint_error is None
                    else endpoint_error
                ),
                action=None if endpoint_error is None else "Use a valid https blob endpoint URL.",
            )
        )

    checks.append(
        DiagnosticCheck(
            id="database_url_shape",
            status="pass" if (database_url is None or is_postgres_url(database_url)) else "fail",
            message=(
                "BACKEND_DATABASE_URL is PostgreSQL-shaped."
                if database_url is not None and is_postgres_url(database_url)
                else "BACKEND_DATABASE_URL is not required for one-click provisioning."
                if database_url is None
                else "BACKEND_DATABASE_URL is not PostgreSQL-shaped."
            ),
            action=None if (database_url is None or is_postgres_url(database_url)) else "Use a postgres:// or postgresql:// URL.",
        )
    )
    checks.extend(_build_runtime_contract_checks(env_values))
    checks.extend(_build_production_posture_checks(env_values=env_values, project_config=project_config))
    if database_url is None:
        checks.append(
            DiagnosticCheck(
                id="managed_database_network_posture",
                status="warn",
                message=(
                    "The current Azure one-click path provisions PostgreSQL with public access for MVP simplicity."
                ),
                action="Validate and tighten database network posture before production use.",
            )
        )

    fqdn: str | None = None
    if cli_ok and subscription_id and resource_group and app_name and environment_name and storage_account and blob_container and postgres_server_name and acr_name:
        checks.extend(_provider_registration_checks(subscription_id))
        checks.append(_resource_group_exists_check(subscription_id, resource_group))
        checks.append(_acr_exists_check(subscription_id, resource_group, acr_name))
        checks.extend(_storage_checks(subscription_id, resource_group, storage_account, blob_container))
        checks.append(_postgres_server_exists_check(subscription_id, resource_group, postgres_server_name))
        checks.append(
            _postgres_database_exists_check(
                subscription_id,
                resource_group,
                postgres_server_name,
                postgres_database_name,
            )
        )
        checks.append(_container_apps_environment_exists_check(subscription_id, resource_group, environment_name))
        app_check, fqdn, app_payload = _container_app_checks(
            subscription_id=subscription_id,
            resource_group=resource_group,
            app_name=app_name,
        )
        checks.extend(app_check)
        if app_payload is not None and blob_endpoint is not None:
            checks.extend(_runtime_contract_checks(app_payload, blob_container, blob_endpoint))

    details = AzureDoctorDetails(
        subscription_id=subscription_id,
        tenant_id=tenant_id,
        region=region,
        resource_group=resource_group,
        environment_name=environment_name,
        app_name=app_name,
        fqdn=fqdn,
        storage_account=storage_account,
        blob_container=blob_container,
        blob_endpoint=blob_endpoint,
        acr_name=acr_name,
        acr_server=acr_server,
        postgres_server_name=postgres_server_name,
        postgres_database_name=postgres_database_name,
    )
    return AzureDoctorEvaluation(
        ok=all(check.status != "fail" for check in checks),
        checks=tuple(checks),
        details=details,
    )


def _provider_registration_checks(subscription_id: str) -> list[DiagnosticCheck]:
    checks: list[DiagnosticCheck] = []
    for namespace in (
        "Microsoft.App",
        "Microsoft.ContainerRegistry",
        "Microsoft.Storage",
        "Microsoft.DBforPostgreSQL",
    ):
        provider = run_az_json(
            [
                "provider",
                "show",
                "--subscription",
                subscription_id,
                "--namespace",
                namespace,
            ]
        )
        state = None
        if provider.ok and isinstance(provider.value, dict):
            state = read_dict_string(provider.value, "registrationState")
        if state == "Registered":
            status = "pass"
            message = f"Resource provider {namespace} is registered."
            action = None
        elif state is not None:
            status = "warn"
            message = f"{namespace} registrationState is {state}; deploy can register it automatically."
            action = f"Run deploy once or register manually with `az provider register --namespace {namespace}`."
        else:
            status = "fail"
            message = provider.message or f"Unable to resolve {namespace} registration state."
            action = f"Run `az provider show --namespace {namespace}` and verify Azure permissions."
        checks.append(
            DiagnosticCheck(
                id=f"az_provider_{namespace.lower().replace('.', '_')}_registered",
                status=status,
                message=message,
                action=action,
            )
        )
    return checks


def _resource_group_exists_check(subscription_id: str, resource_group: str) -> DiagnosticCheck:
    result = run_az_json(
        [
            "group",
            "show",
            "--subscription",
            subscription_id,
            "--name",
            resource_group,
        ]
    )
    exists = result.ok and isinstance(result.value, dict)
    if exists:
        return DiagnosticCheck(
            id="azure_resource_group_exists",
            status="pass",
            message=f"Resource group `{resource_group}` exists.",
        )
    if _looks_like_not_found(result.message):
        return DiagnosticCheck(
            id="azure_resource_group_exists",
            status="warn",
            message=f"Resource group `{resource_group}` is not provisioned yet.",
            action="Run deploy once to auto-provision the resource group.",
        )
    return DiagnosticCheck(
        id="azure_resource_group_exists",
        status="fail",
        message=result.message or f"Unable to resolve resource group `{resource_group}`.",
        action="Verify Azure permissions for resource-group read/create operations.",
    )


def _acr_exists_check(subscription_id: str, resource_group: str, acr_name: str) -> DiagnosticCheck:
    result = run_az_json(
        [
            "acr",
            "show",
            "--subscription",
            subscription_id,
            "--resource-group",
            resource_group,
            "--name",
            acr_name,
        ]
    )
    exists = result.ok and isinstance(result.value, dict)
    if exists:
        return DiagnosticCheck(
            id="azure_acr_exists",
            status="pass",
            message=f"ACR `{acr_name}` exists.",
        )
    if _looks_like_not_found(result.message):
        return DiagnosticCheck(
            id="azure_acr_exists",
            status="warn",
            message=f"ACR `{acr_name}` is not provisioned yet.",
            action="Run deploy to provision ACR.",
        )
    return DiagnosticCheck(
        id="azure_acr_exists",
        status="fail",
        message=result.message or f"Unable to resolve ACR `{acr_name}`.",
        action="Verify ACR permissions in the selected subscription/resource group.",
    )


def _storage_checks(
    subscription_id: str,
    resource_group: str,
    storage_account: str,
    blob_container: str,
) -> list[DiagnosticCheck]:
    checks: list[DiagnosticCheck] = []
    account = run_az_json(
        [
            "storage",
            "account",
            "show",
            "--subscription",
            subscription_id,
            "--resource-group",
            resource_group,
            "--name",
            storage_account,
        ]
    )
    account_ok = account.ok and isinstance(account.value, dict)
    if account_ok:
        checks.append(
            DiagnosticCheck(
                id="azure_storage_account_exists",
                status="pass",
                message=f"Storage account `{storage_account}` exists.",
            )
        )
    elif _looks_like_not_found(account.message):
        checks.append(
            DiagnosticCheck(
                id="azure_storage_account_exists",
                status="warn",
                message=f"Storage account `{storage_account}` is not provisioned yet.",
                action="Run deploy to provision Azure Storage.",
            )
        )
    else:
        checks.append(
            DiagnosticCheck(
                id="azure_storage_account_exists",
                status="fail",
                message=account.message or f"Unable to resolve storage account `{storage_account}`.",
                action="Verify storage account permissions and subscription scope.",
            )
        )
    if not account_ok:
        return checks
    keys = run_az_json(
        [
            "storage",
            "account",
            "keys",
            "list",
            "--subscription",
            subscription_id,
            "--resource-group",
            resource_group,
            "--account-name",
            storage_account,
        ]
    )
    key = _extract_storage_account_key(keys.value) if keys.ok else None
    if key is None:
        checks.append(
            DiagnosticCheck(
                id="azure_blob_container_exists",
                status="fail",
                message=keys.message or "Unable to read storage account keys for container check.",
                action="Grant storage key-list permissions or inspect storage account access policies.",
            )
        )
        return checks
    exists = run_az_json(
        [
            "storage",
            "container",
            "exists",
            "--name",
            blob_container,
            "--account-name",
            storage_account,
            "--account-key",
            key,
        ]
    )
    container_exists = bool(exists.value.get("exists")) if exists.ok and isinstance(exists.value, dict) else False
    if container_exists:
        checks.append(
            DiagnosticCheck(
                id="azure_blob_container_exists",
                status="pass",
                message=f"Blob container `{blob_container}` exists.",
            )
        )
    elif _looks_like_not_found(exists.message):
        checks.append(
            DiagnosticCheck(
                id="azure_blob_container_exists",
                status="warn",
                message=f"Blob container `{blob_container}` is not provisioned yet.",
                action="Run deploy to provision the blob container.",
            )
        )
    else:
        checks.append(
            DiagnosticCheck(
                id="azure_blob_container_exists",
                status="fail",
                message=exists.message or f"Unable to resolve blob container `{blob_container}`.",
                action="Verify storage container permissions and account credentials.",
            )
        )
    return checks


def _postgres_server_exists_check(subscription_id: str, resource_group: str, server_name: str) -> DiagnosticCheck:
    result = run_az_json(
        [
            "postgres",
            "flexible-server",
            "show",
            "--subscription",
            subscription_id,
            "--resource-group",
            resource_group,
            "--name",
            server_name,
        ]
    )
    exists = result.ok and isinstance(result.value, dict)
    if exists:
        return DiagnosticCheck(
            id="azure_postgres_server_exists",
            status="pass",
            message=f"PostgreSQL server `{server_name}` exists.",
        )
    if _looks_like_not_found(result.message):
        return DiagnosticCheck(
            id="azure_postgres_server_exists",
            status="warn",
            message=f"PostgreSQL server `{server_name}` is not provisioned yet.",
            action="Run deploy to provision PostgreSQL.",
        )
    return DiagnosticCheck(
        id="azure_postgres_server_exists",
        status="fail",
        message=result.message or f"Unable to resolve PostgreSQL server `{server_name}`.",
        action="Verify PostgreSQL flexible-server permissions and provider availability.",
    )


def _postgres_database_exists_check(
    subscription_id: str,
    resource_group: str,
    server_name: str,
    database_name: str,
) -> DiagnosticCheck:
    result = run_az_json(
        [
            "postgres",
            "flexible-server",
            "db",
            "show",
            "--subscription",
            subscription_id,
            "--resource-group",
            resource_group,
            "--server-name",
            server_name,
            "--database-name",
            database_name,
        ]
    )
    exists = result.ok and isinstance(result.value, dict)
    if exists:
        return DiagnosticCheck(
            id="azure_postgres_database_exists",
            status="pass",
            message=f"PostgreSQL database `{database_name}` exists.",
        )
    if _looks_like_not_found(result.message):
        return DiagnosticCheck(
            id="azure_postgres_database_exists",
            status="warn",
            message=f"PostgreSQL database `{database_name}` is not provisioned yet.",
            action="Run deploy to provision the database.",
        )
    return DiagnosticCheck(
        id="azure_postgres_database_exists",
        status="fail",
        message=result.message or f"Unable to resolve PostgreSQL database `{database_name}`.",
        action="Verify PostgreSQL database read/create permissions.",
    )


def _container_apps_environment_exists_check(
    subscription_id: str,
    resource_group: str,
    environment_name: str,
) -> DiagnosticCheck:
    result = run_az_json(
        [
            "containerapp",
            "env",
            "show",
            "--subscription",
            subscription_id,
            "--resource-group",
            resource_group,
            "--name",
            environment_name,
        ]
    )
    exists = result.ok and isinstance(result.value, dict)
    if exists:
        return DiagnosticCheck(
            id="azure_container_apps_environment_exists",
            status="pass",
            message=f"Container Apps environment `{environment_name}` exists.",
        )
    if _looks_like_not_found(result.message):
        return DiagnosticCheck(
            id="azure_container_apps_environment_exists",
            status="warn",
            message=f"Container Apps environment `{environment_name}` is not provisioned yet.",
            action="Run deploy to provision the Container Apps environment.",
        )
    return DiagnosticCheck(
        id="azure_container_apps_environment_exists",
        status="fail",
        message=result.message or f"Unable to resolve Container Apps environment `{environment_name}`.",
        action="Verify Container Apps environment permissions in the selected resource group.",
    )


def _container_app_checks(
    *,
    subscription_id: str,
    resource_group: str,
    app_name: str,
) -> tuple[list[DiagnosticCheck], str | None, dict[str, object] | None]:
    checks: list[DiagnosticCheck] = []
    app_result = run_az_json(
        [
            "containerapp",
            "show",
            "--subscription",
            subscription_id,
            "--resource-group",
            resource_group,
            "--name",
            app_name,
        ]
    )
    if not app_result.ok or not isinstance(app_result.value, dict):
        if _looks_like_not_found(app_result.message):
            checks.append(
                DiagnosticCheck(
                    id="container_app_inspectable",
                    status="warn",
                    message=f"Container App `{app_name}` is not provisioned yet.",
                    action="Run deploy to create the app.",
                )
            )
        else:
            checks.append(
                DiagnosticCheck(
                    id="container_app_inspectable",
                    status="fail",
                    message=app_result.message or "Unable to inspect Azure Container App.",
                    action="Verify Container App permissions and resource-group scope.",
                )
            )
        return checks, None, None
    fqdn = _extract_fqdn(app_result.value)
    external_ingress = _extract_external_ingress(app_result.value)
    checks.append(
        DiagnosticCheck(
            id="container_app_inspectable",
            status="pass",
            message="Azure Container App is accessible via Azure CLI.",
        )
    )
    checks.append(
        DiagnosticCheck(
            id="container_app_fqdn_present",
            status="pass" if fqdn else "fail",
            message=(
                f"Container App FQDN: {fqdn}"
                if fqdn
                else "Container App ingress FQDN is missing."
            ),
            action=None if fqdn else "Enable external ingress and redeploy app configuration.",
        )
    )
    checks.append(
        DiagnosticCheck(
            id="container_app_ingress_external",
            status="pass" if external_ingress else "fail",
            message=(
                "Container App ingress is externally accessible."
                if external_ingress
                else "Container App ingress is not external."
            ),
            action=(
                None
                if external_ingress
                else "Enable external ingress on the container app for provider FQDN access."
            ),
        )
    )
    return checks, fqdn, app_result.value


def _looks_like_not_found(message: str | None) -> bool:
    if message is None:
        return False
    normalized = normalize_optional_text(message)
    if normalized is None:
        return False
    text = normalized.lower()
    return (
        "not found" in text
        or "could not be found" in text
        or "resourcenotfound" in text
        or "resourcegroupnotfound" in text
    )


def _runtime_contract_checks(
    container_app_payload: dict[str, object],
    blob_container: str,
    blob_endpoint: str,
) -> list[DiagnosticCheck]:
    checks: list[DiagnosticCheck] = []
    env_map = _container_app_env_map(container_app_payload)

    checks.append(
        _env_value_check(
            env_map,
            "BACKEND_STORAGE_BACKEND",
            "managed",
            "runtime_storage_backend_managed",
        )
    )
    checks.append(
        _env_value_check(
            env_map,
            "BACKEND_OBJECT_STORE_PROVIDER",
            "azure_blob",
            "runtime_object_store_provider_azure_blob",
        )
    )
    checks.append(
        _env_value_check(
            env_map,
            "BACKEND_OBJECT_STORE_NAME",
            blob_container,
            "runtime_object_store_name_matches_blob_container",
        )
    )
    checks.append(
        _env_value_check(
            env_map,
            "BACKEND_OBJECT_STORE_ENDPOINT",
            blob_endpoint,
            "runtime_object_store_endpoint_matches_blob_endpoint",
        )
    )
    db_entry = env_map.get("BACKEND_DATABASE_URL")
    db_ok = False
    if db_entry is not None:
        value, secret_ref = db_entry
        db_ok = bool(secret_ref) or (value is not None and is_postgres_url(value))
    checks.append(
        DiagnosticCheck(
            id="runtime_database_url_configured",
            status="pass" if db_ok else "fail",
            message=(
                "BACKEND_DATABASE_URL is configured (secretRef or PostgreSQL URL)."
                if db_ok
                else "BACKEND_DATABASE_URL is missing from the Container App runtime env."
            ),
            action=None if db_ok else "Run deploy to set BACKEND_DATABASE_URL.",
        )
    )
    return checks


def _container_app_env_map(payload: dict[str, object]) -> dict[str, tuple[str | None, str | None]]:
    result: dict[str, tuple[str | None, str | None]] = {}
    properties = payload.get("properties")
    if not isinstance(properties, dict):
        return result
    template = properties.get("template")
    if not isinstance(template, dict):
        return result
    containers = template.get("containers")
    if not isinstance(containers, list) or not containers:
        return result
    first_container = containers[0]
    if not isinstance(first_container, dict):
        return result
    env_entries = first_container.get("env")
    if not isinstance(env_entries, list):
        return result
    for item in env_entries:
        if not isinstance(item, dict):
            continue
        name = read_dict_string(item, "name")
        if name is None:
            continue
        value = read_dict_string(item, "value")
        secret_ref = read_dict_string(item, "secretRef")
        result[name] = (value, secret_ref)
    return result


def _env_value_check(
    env_map: dict[str, tuple[str | None, str | None]],
    key: str,
    expected_value: str,
    check_id: str,
) -> DiagnosticCheck:
    entry = env_map.get(key)
    if entry is None:
        return DiagnosticCheck(
            id=check_id,
            status="fail",
            message=f"{key} is missing from Container App runtime env.",
            action=f"Run deploy to set {key}.",
        )
    value, secret_ref = entry
    if secret_ref:
        return DiagnosticCheck(
            id=check_id,
            status="fail",
            message=f"{key} is configured as a secretRef and cannot be validated against `{expected_value}`.",
            action=f"Set {key} as explicit value `{expected_value}` in deploy runtime env.",
        )
    ok = value == expected_value
    return DiagnosticCheck(
        id=check_id,
        status="pass" if ok else "fail",
        message=f"{key}={value}" if ok else f"{key} is `{value}`, expected `{expected_value}`.",
        action=None if ok else f"Run deploy to enforce {key}={expected_value}.",
    )


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


def _stable_suffix(seed: str, *, length: int) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return digest[:length]


def _sanitize_name_token(value: str) -> str:
    lowered = value.strip().lower()
    return "".join(char for char in lowered if char.isalnum())


def _build_storage_account_name(app_name: str, suffix: str) -> str:
    token = _sanitize_name_token(app_name) or "portworld"
    return f"pw{token}{suffix}"[:24]


def _build_acr_name(app_name: str, suffix: str) -> str:
    token = _sanitize_name_token(app_name) or "portworld"
    candidate = f"pw{token}{suffix}"
    if len(candidate) < 5:
        candidate = f"{candidate}acr"
    return candidate[:50]


def _build_postgres_server_name(app_name: str, suffix: str) -> str:
    token = _sanitize_name_token(app_name) or "portworld"
    return f"pwpg{token}{suffix}"[:63]


def _extract_fqdn(container_app_payload: dict[str, object]) -> str | None:
    properties = container_app_payload.get("properties")
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


def _required_check(check_id: str, value: str | None, action: str) -> DiagnosticCheck:
    return DiagnosticCheck(
        id=check_id,
        status="pass" if value else "fail",
        message=f"Resolved value: {value}" if value else "Required value is missing.",
        action=None if value else action,
    )


def _first_non_empty(*values: str | None) -> str | None:
    for value in values:
        normalized = normalize_optional_text(value)
        if normalized is not None:
            return normalized
    return None


def _build_runtime_contract_checks(env_values: dict[str, str]) -> list[DiagnosticCheck]:
    storage_backend = _first_non_empty(env_values.get("BACKEND_STORAGE_BACKEND"))
    object_store_provider = _first_non_empty(env_values.get("BACKEND_OBJECT_STORE_PROVIDER"))
    return [
        DiagnosticCheck(
            id="managed_storage_backend_contract",
            status="pass" if storage_backend == "managed" else "warn",
            message=(
                "BACKEND_STORAGE_BACKEND is set to managed."
                if storage_backend == "managed"
                else "BACKEND_STORAGE_BACKEND is not set to managed in the current workspace config."
            ),
            action=(
                None
                if storage_backend == "managed"
                else "The deploy path will override this to managed for Azure."
            ),
        ),
        DiagnosticCheck(
            id="managed_object_store_provider_contract",
            status="pass" if object_store_provider == "azure_blob" else "warn",
            message=(
                "BACKEND_OBJECT_STORE_PROVIDER is set to azure_blob."
                if object_store_provider == "azure_blob"
                else "BACKEND_OBJECT_STORE_PROVIDER is not set to azure_blob in the current workspace config."
            ),
            action=(
                None
                if object_store_provider == "azure_blob"
                else "The deploy path will override this to azure_blob for Azure."
            ),
        ),
    ]


def _build_production_posture_checks(
    *,
    env_values: dict[str, str],
    project_config: ProjectConfig | None,
) -> list[DiagnosticCheck]:
    backend_profile = _first_non_empty(
        env_values.get("BACKEND_PROFILE"),
        None if project_config is None else project_config.security.backend_profile,
    )
    cors_origins = _first_non_empty(
        env_values.get("CORS_ORIGINS"),
        None if project_config is None else ",".join(project_config.security.cors_origins),
    )
    allowed_hosts = _first_non_empty(
        env_values.get("BACKEND_ALLOWED_HOSTS"),
        None if project_config is None else ",".join(project_config.security.allowed_hosts),
    )
    return [
        DiagnosticCheck(
            id="production_backend_profile",
            status="pass" if backend_profile == "production" else "warn",
            message=(
                "BACKEND_PROFILE is production."
                if backend_profile == "production"
                else "BACKEND_PROFILE is not explicitly set to production."
            ),
            action=(
                None
                if backend_profile == "production"
                else "The deploy path will force production settings, but recording them in config is recommended."
            ),
        ),
        DiagnosticCheck(
            id="production_cors_explicit",
            status="pass" if _is_explicit_production_value(cors_origins) else "warn",
            message=(
                "CORS origins are explicitly configured."
                if _is_explicit_production_value(cors_origins)
                else "CORS origins are unset or still use a wildcard/default posture."
            ),
            action="Set explicit production CORS origins before deploy.",
        ),
        DiagnosticCheck(
            id="production_allowed_hosts_explicit",
            status="pass" if _is_explicit_production_value(allowed_hosts) else "warn",
            message=(
                "Allowed hosts are explicitly configured."
                if _is_explicit_production_value(allowed_hosts)
                else "Allowed hosts are unset or still use a wildcard/default posture."
            ),
            action="Set explicit production allowed hosts before deploy.",
        ),
    ]


def _is_explicit_production_value(value: str | None) -> bool:
    return bool(value and value.strip() and value.strip() != "*")


def _extract_external_ingress(container_app_payload: dict[str, object]) -> bool:
    properties = container_app_payload.get("properties")
    if not isinstance(properties, dict):
        return False
    configuration = properties.get("configuration")
    if not isinstance(configuration, dict):
        return False
    ingress = configuration.get("ingress")
    if not isinstance(ingress, dict):
        return False
    external = ingress.get("external")
    return bool(external)
