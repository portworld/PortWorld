from __future__ import annotations

from portworld_cli.azure.client import AzureAdapters
from portworld_cli.azure.common import read_dict_string
from portworld_cli.azure.stages.config import ResolvedAzureDeployConfig
from portworld_cli.azure.stages.shared import stage_ok
from portworld_cli.deploy.config import DeployStageError


def ensure_resource_group(
    config: ResolvedAzureDeployConfig,
    *,
    stage_records: list[dict[str, object]],
    adapters: AzureAdapters,
) -> None:
    current = adapters.compute.run_json(
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
        stage_records.append(stage_ok("resource_group", f"Using resource group `{config.resource_group}`."))
        return
    created = adapters.compute.run_json(
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
    stage_records.append(stage_ok("resource_group", f"Created resource group `{config.resource_group}`."))


def ensure_resource_provider(
    config: ResolvedAzureDeployConfig,
    *,
    stage_records: list[dict[str, object]],
    adapters: AzureAdapters,
    namespace: str,
) -> None:
    provider = adapters.compute.run_json(
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
        stage_records.append(stage_ok("provider_registration", f"{namespace} is registered."))
        return
    register = adapters.compute.run_json(
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
    stage_records.append(stage_ok("provider_registration", f"Requested registration for {namespace}."))


def ensure_acr(
    config: ResolvedAzureDeployConfig,
    *,
    stage_records: list[dict[str, object]],
    adapters: AzureAdapters,
) -> tuple[str, str, str]:
    acr = adapters.image.run_json(
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
        create = adapters.image.run_json(
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
        update = adapters.image.run_json(
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
    creds = adapters.image.run_json(
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
    password = extract_acr_password(creds.value)
    if username is None or password is None:
        raise DeployStageError(
            stage="acr_credentials",
            message="ACR credentials are incomplete.",
            action="Regenerate ACR credentials and retry deploy.",
        )
    message = "Created ACR" if created else "Using existing ACR"
    stage_records.append(stage_ok("acr_provision", f"{message} `{config.acr_name}` ({login_server})."))
    return login_server, username, password


def extract_acr_password(payload: dict[str, object]) -> str | None:
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


def ensure_storage(
    config: ResolvedAzureDeployConfig,
    *,
    stage_records: list[dict[str, object]],
    adapters: AzureAdapters,
) -> None:
    account = adapters.storage.run_json(
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
        create = adapters.storage.run_json(
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
    keys = adapters.storage.run_json(
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
    account_key = extract_storage_account_key(keys.value) if keys.ok else None
    if account_key is None:
        raise DeployStageError(
            stage="storage_provision",
            message=keys.message or "Unable to resolve storage account key.",
            action="Verify storage account key permissions.",
        )
    exists = adapters.storage.run_json(
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
        create_container = adapters.storage.run_json(
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
    stage_records.append(
        stage_ok(
            "storage_provision",
            f"{message} `{config.storage_account}` and validated container `{config.blob_container}`.",
        )
    )


def extract_storage_account_key(value: object) -> str | None:
    if not isinstance(value, list):
        return None
    for item in value:
        if not isinstance(item, dict):
            continue
        key = read_dict_string(item, "value")
        if key is not None:
            return key
    return None
