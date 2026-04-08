from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from time import monotonic, sleep

from portworld_cli.azure.client import AzureAdapters
from portworld_cli.azure.stages.artifacts import (
    ensure_acr,
    ensure_resource_group,
    ensure_resource_provider,
    ensure_storage,
)
from portworld_cli.azure.stages.config import ResolvedAzureDeployConfig
from portworld_cli.azure.stages.shared import stage_ok, to_azure_secret_name
from portworld_cli.deploy.config import DeployStageError
from portworld_cli.deploy.reporting import humanize_stage_label
from portworld_cli.ux.progress import ProgressReporter


@dataclass(frozen=True, slots=True)
class AzureDeployMutationResult:
    fqdn: str | None
    image_uri: str


def run_azure_deploy_mutations(
    *,
    config: ResolvedAzureDeployConfig,
    env_values: OrderedDict[str, str],
    stage_records: list[dict[str, object]],
    adapters: AzureAdapters,
    progress: ProgressReporter,
) -> AzureDeployMutationResult:
    with progress.stage(humanize_stage_label("azure_subscription_set")):
        set_subscription = adapters.compute.run_json(["account", "set", "--subscription", config.subscription_id])
        if not set_subscription.ok:
            raise DeployStageError(
                stage="subscription_set",
                message=set_subscription.message or "Unable to set active Azure subscription.",
                action="Verify subscription id and az login context.",
            )
        stage_records.append(stage_ok("subscription_set", f"Using subscription `{config.subscription_id}`."))

    with progress.stage(humanize_stage_label("azure_platform_setup")):
        ensure_resource_group(config, stage_records=stage_records, adapters=adapters)
        ensure_resource_provider(
            config,
            stage_records=stage_records,
            adapters=adapters,
            namespace="Microsoft.App",
        )
        ensure_resource_provider(
            config,
            stage_records=stage_records,
            adapters=adapters,
            namespace="Microsoft.ContainerRegistry",
        )
        ensure_resource_provider(
            config,
            stage_records=stage_records,
            adapters=adapters,
            namespace="Microsoft.Storage",
        )

    with progress.stage(humanize_stage_label("azure_registry_setup")):
        acr_server, acr_username, acr_password = ensure_acr(config, stage_records=stage_records, adapters=adapters)
        image_uri = f"{acr_server}/{config.acr_repo}:{config.image_tag}"

    with progress.stage(humanize_stage_label("azure_runtime_infra")):
        ensure_storage(config, stage_records=stage_records, adapters=adapters)
        ensure_container_apps_environment(config, stage_records=stage_records, adapters=adapters)

    current_app = adapters.compute.run_json(
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
        stage_records.append(stage_ok("container_app_lookup", f"Found Container App `{config.app_name}`."))
    else:
        stage_records.append(stage_ok("container_app_lookup", f"Container App `{config.app_name}` will be created."))

    runtime_env = build_runtime_env_vars(env_values, config)
    plain_env, secret_env = split_runtime_env_for_azure(runtime_env)
    env_args = [f"{key}={value}" for key, value in plain_env.items()]
    env_args.extend(f"{key}=secretref:{secret_name}" for key, secret_name in secret_env.items())

    with progress.stage(humanize_stage_label("azure_container_app_deploy")):
        if app_exists:
            set_container_app_registry_credentials(
                config=config,
                acr_server=acr_server,
                acr_username=acr_username,
                acr_password=acr_password,
                adapters=adapters,
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
            update_response = adapters.compute.run_json(update_args)
            if not update_response.ok:
                raise DeployStageError(
                    stage="container_app_update",
                    message=update_response.message or "Unable to update Azure Container App.",
                    action="Verify app permissions and containerapp update arguments.",
                )
            stage_records.append(stage_ok("container_app_update", f"Updated image to `{image_uri}`."))
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
            create_response = adapters.compute.run_json(create_args)
            if not create_response.ok:
                raise DeployStageError(
                    stage="container_app_create",
                    message=create_response.message or "Unable to create Azure Container App.",
                    action="Verify Container Apps permissions, image accessibility, and environment setup.",
                )
            stage_records.append(stage_ok("container_app_create", f"Created Container App `{config.app_name}`."))

    with progress.stage(humanize_stage_label("azure_rollout_wait")):
        fqdn = wait_for_container_app_readiness(config=config, adapters=adapters)
        if fqdn is None:
            raise DeployStageError(
                stage="container_app_wait_ready",
                message="Container App did not report a ready external revision in time.",
                action="Inspect Container App revisions and ingress settings.",
            )
        stage_records.append(stage_ok("container_app_wait_ready", f"Container App is ready at `{fqdn}`."))
    return AzureDeployMutationResult(fqdn=fqdn, image_uri=image_uri)


def ensure_container_apps_environment(
    config: ResolvedAzureDeployConfig,
    *,
    stage_records: list[dict[str, object]],
    adapters: AzureAdapters,
) -> None:
    env = adapters.compute.run_json(
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
        stage_records.append(stage_ok("container_apps_environment", f"Using environment `{config.environment_name}`."))
        return
    create = adapters.compute.run_json(
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
    stage_records.append(stage_ok("container_apps_environment", f"Created environment `{config.environment_name}`."))


def resolve_container_app_fqdn(
    config: ResolvedAzureDeployConfig,
    *,
    adapters: AzureAdapters,
) -> str | None:
    response = adapters.compute.run_json(
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


def set_container_app_registry_credentials(
    *,
    config: ResolvedAzureDeployConfig,
    acr_server: str,
    acr_username: str,
    acr_password: str,
    adapters: AzureAdapters,
) -> None:
    response = adapters.compute.run_json(
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


def wait_for_container_app_readiness(
    *,
    config: ResolvedAzureDeployConfig,
    adapters: AzureAdapters,
    timeout_seconds: float = 300.0,
    poll_interval_seconds: float = 5.0,
) -> str | None:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        app = adapters.compute.run_json(
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
            fqdn = extract_fqdn_and_external(app.value)
            if fqdn is not None and revision_is_ready(app.value):
                return fqdn
        sleep(poll_interval_seconds)
    return None


def revision_is_ready(payload: dict[str, object]) -> bool:
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


def extract_fqdn_and_external(payload: dict[str, object]) -> str | None:
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


def build_runtime_env_vars(
    env_values: OrderedDict[str, str],
    config: ResolvedAzureDeployConfig,
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
        "BACKEND_STORAGE_BACKEND",
        "PORT",
    }
    for key, value in env_values.items():
        if key in excluded:
            continue
        final_env[key] = value

    final_env["BACKEND_PROFILE"] = "production"
    final_env["BACKEND_OBJECT_STORE_PROVIDER"] = "azure_blob"
    final_env["BACKEND_OBJECT_STORE_NAME"] = config.blob_container
    final_env["BACKEND_OBJECT_STORE_ENDPOINT"] = config.blob_endpoint
    final_env["BACKEND_OBJECT_STORE_PREFIX"] = config.app_name
    return final_env


def sanitize_runtime_env_for_output(runtime_env: OrderedDict[str, str]) -> OrderedDict[str, str]:
    redacted: OrderedDict[str, str] = OrderedDict()
    for key, value in runtime_env.items():
        upper = key.upper()
        if (
            "TOKEN" in upper
            or "SECRET" in upper
            or "PASSWORD" in upper
            or upper.endswith("_KEY")
        ):
            redacted[key] = "***REDACTED***"
        else:
            redacted[key] = value
    return redacted


def split_runtime_env_for_azure(runtime_env: OrderedDict[str, str]) -> tuple[OrderedDict[str, str], OrderedDict[str, str]]:
    plain_env: OrderedDict[str, str] = OrderedDict()
    secret_env: OrderedDict[str, str] = OrderedDict()
    for key, value in runtime_env.items():
        if is_sensitive_env_key(key):
            secret_env[key] = to_azure_secret_name(key)
        else:
            plain_env[key] = value
    return plain_env, secret_env


def is_sensitive_env_key(key: str) -> bool:
    upper = key.upper()
    return (
        "TOKEN" in upper
        or "SECRET" in upper
        or "PASSWORD" in upper
        or upper.endswith("_KEY")
    )
