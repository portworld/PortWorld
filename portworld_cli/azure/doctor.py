from __future__ import annotations

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
    resource_group = _first_non_empty(
        explicit_resource_group,
        None if azure_defaults is None else azure_defaults.resource_group,
    )
    region = _first_non_empty(
        explicit_region,
        None if azure_defaults is None else azure_defaults.region,
    )
    environment_name = _first_non_empty(
        explicit_environment,
        None if azure_defaults is None else azure_defaults.environment_name,
    )
    app_name = _first_non_empty(
        explicit_app,
        None if azure_defaults is None else azure_defaults.app_name,
    )
    database_url = _first_non_empty(explicit_database_url, env_values.get("BACKEND_DATABASE_URL"))
    storage_account = _first_non_empty(explicit_storage_account)
    if storage_account is None and azure_defaults is not None:
        storage_account = _first_non_empty(azure_defaults.storage_account)
    blob_container = _first_non_empty(
        explicit_blob_container,
        env_values.get("BACKEND_OBJECT_STORE_NAME"),
    )
    blob_endpoint = _first_non_empty(
        explicit_blob_endpoint,
        env_values.get("BACKEND_OBJECT_STORE_ENDPOINT"),
    )

    cli_ok = azure_cli_available()
    checks.append(
        DiagnosticCheck(
            id="az_cli_installed",
            status="pass" if cli_ok else "fail",
            message="Azure CLI is installed" if cli_ok else "Azure CLI is not installed or not on PATH.",
            action=None if cli_ok else "Install Azure CLI and retry doctor.",
        )
    )

    account_subscription_id: str | None = None
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
            account_subscription_id = read_dict_string(account.value, "id")
            tenant_id = read_dict_string(account.value, "tenantId")
            if subscription_id is None:
                subscription_id = account_subscription_id
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

        provider = run_az_json(["provider", "show", "--namespace", "Microsoft.App"])
        provider_state: str | None = None
        if provider.ok and isinstance(provider.value, dict):
            provider_state = read_dict_string(provider.value, "registrationState")
        checks.append(
            DiagnosticCheck(
                id="az_provider_microsoft_app_registered",
                status="pass" if provider_state == "Registered" else "fail",
                message=(
                    "Resource provider Microsoft.App is registered."
                    if provider_state == "Registered"
                    else (
                        f"Microsoft.App registrationState is {provider_state}."
                        if provider_state
                        else (provider.message or "Unable to resolve Microsoft.App registration state.")
                    )
                ),
                action=(
                    None
                    if provider_state == "Registered"
                    else "Register provider: `az provider register --namespace Microsoft.App`."
                ),
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

    db_ok = bool(database_url and is_postgres_url(database_url))
    checks.append(
        DiagnosticCheck(
            id="database_url_ready",
            status="pass" if db_ok else "fail",
            message=(
                "BACKEND_DATABASE_URL is present and uses a PostgreSQL scheme."
                if db_ok
                else "BACKEND_DATABASE_URL is missing or not PostgreSQL-shaped."
            ),
            action=None if db_ok else "Set BACKEND_DATABASE_URL to an existing PostgreSQL URL.",
        )
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

    fqdn: str | None = None
    if cli_ok and subscription_id and resource_group and app_name:
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
            checks.append(
                DiagnosticCheck(
                    id="container_app_inspectable",
                    status="fail",
                    message=app_result.message or "Unable to inspect Azure Container App.",
                    action="Verify app name, resource group, subscription, and az permissions.",
                )
            )
        else:
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
    )
    return AzureDoctorEvaluation(
        ok=all(check.status != "fail" for check in checks),
        checks=tuple(checks),
        details=details,
    )


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
