from __future__ import annotations

import os
from collections import OrderedDict
from contextlib import contextmanager
from typing import Iterator

from portworld_cli.deploy.config import DeployStageError, ResolvedDeployConfig
from portworld_cli.deploy.gcp_errors import gcp_error_action, gcp_error_message
from portworld_shared.backend_env import validate_backend_env_contract
from portworld_shared.providers import list_provider_requirements
from portworld_shared.runtime_secrets import ADDITIONAL_DEPLOY_SENSITIVE_ENV_KEYS


_PROVIDER_SECRET_ENV_KEYS: tuple[str, ...] = tuple(
    key.strip()
    for entry in list_provider_requirements()
    for key in (
        *entry.secret_binding.required_env_keys,
        *entry.secret_binding.optional_env_keys,
    )
    if key.strip()
)
_DEPRECATED_SENSITIVE_ENV_KEYS: tuple[str, ...] = (
    "VISION_PROVIDER_API_KEY",
    "VISION_PROVIDER_BASE_URL",
    "MISTRAL_API_KEY",
    "MISTRAL_BASE_URL",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
)
_CORE_SENSITIVE_ENV_KEYS: tuple[str, ...] = tuple(
    dict.fromkeys(
        (
            *_PROVIDER_SECRET_ENV_KEYS,
            *_DEPRECATED_SENSITIVE_ENV_KEYS,
            *ADDITIONAL_DEPLOY_SENSITIVE_ENV_KEYS,
            "BACKEND_BEARER_TOKEN",
        )
    ).keys()
)
LOCAL_ONLY_ENV_KEYS: tuple[str, ...] = (
    "BACKEND_DATA_DIR",
    "BACKEND_SQLITE_PATH",
    "PORT",
)


def _effective_sensitive_env_keys(env_values: OrderedDict[str, str]) -> tuple[str, ...]:
    del env_values
    return _CORE_SENSITIVE_ENV_KEYS


def build_runtime_env_vars(
    *,
    env_values: OrderedDict[str, str],
    config: ResolvedDeployConfig,
    bucket_name: str,
) -> dict[str, str]:
    sensitive_env_keys = _effective_sensitive_env_keys(env_values)
    final_env: OrderedDict[str, str] = OrderedDict()
    for key, value in env_values.items():
        if key in sensitive_env_keys or key in LOCAL_ONLY_ENV_KEYS:
            continue
        final_env[key] = value

    final_env["BACKEND_PROFILE"] = "production"
    final_env["BACKEND_OBJECT_STORE_PROVIDER"] = "gcs"
    final_env["BACKEND_OBJECT_STORE_NAME"] = bucket_name
    final_env["BACKEND_OBJECT_STORE_PREFIX"] = config.service_name
    return dict(final_env)


def build_cloud_run_secret_bindings(
    *,
    provider_secret_names: dict[str, str],
    bearer_secret_name: str,
) -> dict[str, str]:
    bindings: dict[str, str] = {
        "BACKEND_BEARER_TOKEN": f"{bearer_secret_name}:latest",
    }
    for env_key, secret_name in provider_secret_names.items():
        bindings[env_key] = f"{secret_name}:latest"
    return bindings


def validate_final_settings(
    *,
    env_vars: dict[str, str],
    env_values: OrderedDict[str, str],
    secret_placeholders: dict[str, str],
) -> None:
    sensitive_env_keys = _effective_sensitive_env_keys(env_values)
    combined_env = dict(env_vars)
    for key in sensitive_env_keys:
        local_value = (env_values.get(key, "") or "").strip()
        if local_value:
            combined_env[key] = local_value
    combined_env.update(secret_placeholders)
    contract = validate_backend_env_contract(combined_env)
    if contract.backend_object_store_provider == "filesystem":
        raise RuntimeError(
            "Managed Cloud Run deploy requires a managed object store provider."
        )
    if contract.backend_object_store_provider != "gcs":
        raise RuntimeError(
            "Managed Cloud Run deploy requires BACKEND_OBJECT_STORE_PROVIDER=gcs."
        )


def deploy_cloud_run_service(
    *,
    adapters: GCPAdapters,
    config: ResolvedDeployConfig,
    image_uri: str,
    service_account_email: str,
    env_vars: dict[str, str],
    secret_bindings: dict[str, str],
    default_timeout: str,
    ingress_setting: str,
):
    result = adapters.cloud_run.deploy_service(
        project_id=config.project_id,
        region=config.region,
        service_name=config.service_name,
        image_uri=image_uri,
        service_account_email=service_account_email,
        env_vars=env_vars,
        secrets=secret_bindings,
        cloudsql_connection_name=None,
        timeout=default_timeout,
        cpu=config.cpu,
        memory=config.memory,
        min_instances=config.min_instances,
        max_instances=config.max_instances,
        concurrency=config.concurrency,
        allow_unauthenticated=True,
        ingress=ingress_setting,
    )
    if not result.ok:
        raise DeployStageError(
            stage="cloud_run_deploy",
            message=gcp_error_message(result.error, "Cloud Run deploy failed."),
            action=gcp_error_action(result.error, "Inspect the Cloud Run error output and rerun deploy."),
        )
    assert result.value is not None
    return result.value


def _ensure_secret_version(
    *,
    adapters: GCPAdapters,
    project_id: str,
    secret_name: str,
    secret_value: str,
    stage: str,
) -> str:
    _ensure_secret_exists(
        adapters=adapters,
        project_id=project_id,
        secret_name=secret_name,
        stage=stage,
    )
    _add_secret_version(
        adapters=adapters,
        project_id=project_id,
        secret_name=secret_name,
        secret_value=secret_value,
        stage=stage,
    )
    return secret_name


def _ensure_secret_exists(
    *,
    adapters: GCPAdapters,
    project_id: str,
    secret_name: str,
    stage: str,
) -> None:
    result = adapters.secret_manager.create_secret(
        project_id=project_id,
        secret_name=secret_name,
    )
    if not result.ok:
        raise DeployStageError(
            stage=stage,
            message=gcp_error_message(result.error, f"Failed creating secret {secret_name!r}."),
            action=gcp_error_action(result.error, "Verify Secret Manager permissions and rerun deploy."),
        )


def _add_secret_version(
    *,
    adapters: GCPAdapters,
    project_id: str,
    secret_name: str,
    secret_value: str,
    stage: str,
) -> None:
    result = adapters.secret_manager.add_secret_version(
        project_id=project_id,
        secret_name=secret_name,
        secret_value=secret_value,
    )
    if not result.ok:
        raise DeployStageError(
            stage=stage,
            message=gcp_error_message(result.error, f"Failed adding secret version for {secret_name!r}."),
            action=gcp_error_action(result.error, "Verify Secret Manager permissions and rerun deploy."),
        )


def _service_secret_name(service_name: str, suffix: str) -> str:
    import re

    normalized_service = re.sub(r"[^a-z0-9-]+", "-", service_name.strip().lower()).strip("-")
    return f"{normalized_service}-{suffix}"


@contextmanager
def _temporary_environ(overrides: dict[str, str]) -> Iterator[None]:
    original = os.environ.copy()
    try:
        os.environ.clear()
        os.environ.update(original)
        os.environ.update(overrides)
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)
