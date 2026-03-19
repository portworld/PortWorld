from __future__ import annotations

import os
from collections import OrderedDict
from contextlib import contextmanager
from typing import Any, Iterator

from backend.core.provider_requirements import list_provider_requirements
from portworld_cli.deploy.config import DeployStageError, ResolvedDeployConfig
from portworld_cli.gcp import GCPAdapters, build_postgres_url


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
            "BACKEND_BEARER_TOKEN",
            "BACKEND_DATABASE_URL",
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


def ensure_cloud_sql(
    *,
    adapters: GCPAdapters,
    config: ResolvedDeployConfig,
    default_sql_database_version: str,
    default_sql_cpu_count: int,
    default_sql_memory: str,
    default_sql_user_name: str,
) -> tuple[Any, str, str]:
    instance_result = adapters.cloud_sql.create_instance(
        project_id=config.project_id,
        region=config.region,
        instance_name=config.sql_instance_name,
        database_version=default_sql_database_version,
        cpu_count=default_sql_cpu_count,
        memory=default_sql_memory,
    )
    if not instance_result.ok:
        raise DeployStageError(
            stage="cloud_sql_setup",
            message=_gcp_error_message(instance_result.error, "Failed creating Cloud SQL instance."),
            action=_gcp_error_action(instance_result.error, "Verify Cloud SQL Admin permissions and retry."),
        )
    assert instance_result.value is not None
    instance_ref = instance_result.value.resource

    database_result = adapters.cloud_sql.create_database(
        project_id=config.project_id,
        instance_name=config.sql_instance_name,
        database_name=config.database_name,
    )
    if not database_result.ok:
        raise DeployStageError(
            stage="cloud_sql_setup",
            message=_gcp_error_message(database_result.error, "Failed creating Cloud SQL database."),
            action=_gcp_error_action(database_result.error, "Verify Cloud SQL permissions and retry."),
        )

    db_password = _generate_secure_token(length=24)
    user_result = adapters.cloud_sql.create_or_update_user(
        project_id=config.project_id,
        instance_name=config.sql_instance_name,
        user_name=default_sql_user_name,
        password=db_password,
    )
    if not user_result.ok:
        raise DeployStageError(
            stage="cloud_sql_setup",
            message=_gcp_error_message(user_result.error, "Failed creating or updating the Cloud SQL application user."),
            action=_gcp_error_action(user_result.error, "Verify Cloud SQL permissions and retry."),
        )

    if not instance_ref.connection_name or not instance_ref.primary_ip_address:
        refreshed = adapters.cloud_sql.get_instance(
            project_id=config.project_id,
            instance_name=config.sql_instance_name,
        )
        if not refreshed.ok:
            raise DeployStageError(
                stage="cloud_sql_setup",
                message=_gcp_error_message(refreshed.error, "Failed refreshing Cloud SQL instance details."),
                action=_gcp_error_action(refreshed.error, "Wait for the instance to finish provisioning and rerun deploy."),
            )
        if refreshed.value is not None:
            instance_ref = refreshed.value

    if instance_ref.connection_name:
        database_url = build_postgres_url(
            username=default_sql_user_name,
            password=db_password,
            database_name=config.database_name,
            unix_socket_path=f"/cloudsql/{instance_ref.connection_name}",
        )
    elif instance_ref.primary_ip_address:
        database_url = build_postgres_url(
            username=default_sql_user_name,
            password=db_password,
            database_name=config.database_name,
            host=instance_ref.primary_ip_address,
        )
    else:
        raise DeployStageError(
            stage="cloud_sql_setup",
            message="Cloud SQL instance does not expose a connection name or primary IP address yet.",
            action="Wait for the instance to finish provisioning, then rerun deploy.",
        )
    database_url_secret_name = _service_secret_name(config.service_name, "backend-database-url")
    _ensure_secret_version(
        adapters=adapters,
        project_id=config.project_id,
        secret_name=database_url_secret_name,
        secret_value=database_url,
        stage="cloud_sql_setup",
    )
    return instance_ref, database_url_secret_name, database_url


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
    final_env["BACKEND_STORAGE_BACKEND"] = "managed"
    final_env["BACKEND_OBJECT_STORE_PROVIDER"] = "gcs"
    final_env["BACKEND_OBJECT_STORE_NAME"] = bucket_name
    final_env["BACKEND_OBJECT_STORE_BUCKET"] = bucket_name
    final_env["BACKEND_OBJECT_STORE_PREFIX"] = config.service_name
    final_env["CORS_ORIGINS"] = config.cors_origins
    final_env["BACKEND_ALLOWED_HOSTS"] = config.allowed_hosts
    final_env["BACKEND_DEBUG_TRACE_WS_MESSAGES"] = "false"
    return dict(final_env)


def build_cloud_run_secret_bindings(
    *,
    provider_secret_names: dict[str, str],
    bearer_secret_name: str,
    database_url_secret_name: str,
) -> dict[str, str]:
    bindings: dict[str, str] = {
        "BACKEND_BEARER_TOKEN": f"{bearer_secret_name}:latest",
        "BACKEND_DATABASE_URL": f"{database_url_secret_name}:latest",
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
    from backend.core.settings import Settings

    sensitive_env_keys = _effective_sensitive_env_keys(env_values)
    combined_env = dict(env_vars)
    for key in sensitive_env_keys:
        local_value = (env_values.get(key, "") or "").strip()
        if local_value:
            combined_env[key] = local_value
    combined_env.update(secret_placeholders)
    with _temporary_environ(combined_env):
        settings = Settings.from_env()
        settings.validate_production_posture()
        settings.validate_storage_contract()


def deploy_cloud_run_service(
    *,
    adapters: GCPAdapters,
    config: ResolvedDeployConfig,
    image_uri: str,
    service_account_email: str,
    env_vars: dict[str, str],
    secret_bindings: dict[str, str],
    sql_instance_ref: Any,
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
        cloudsql_connection_name=sql_instance_ref.connection_name,
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
            message=_gcp_error_message(result.error, "Cloud Run deploy failed."),
            action=_gcp_error_action(result.error, "Inspect the Cloud Run error output and rerun deploy."),
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
            message=_gcp_error_message(result.error, f"Failed creating secret {secret_name!r}."),
            action=_gcp_error_action(result.error, "Verify Secret Manager permissions and rerun deploy."),
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
            message=_gcp_error_message(result.error, f"Failed adding secret version for {secret_name!r}."),
            action=_gcp_error_action(result.error, "Verify Secret Manager permissions and rerun deploy."),
        )


def _service_secret_name(service_name: str, suffix: str) -> str:
    import re

    normalized_service = re.sub(r"[^a-z0-9-]+", "-", service_name.strip().lower()).strip("-")
    return f"{normalized_service}-{suffix}"


def _generate_secure_token(*, length: int = 32) -> str:
    import secrets

    return secrets.token_urlsafe(length)


def _gcp_error_message(error: object | None, fallback: str) -> str:
    message = getattr(error, "message", None)
    if isinstance(message, str) and message.strip():
        return message
    return fallback


def _gcp_error_action(error: object | None, fallback: str) -> str:
    action = getattr(error, "action", None)
    if isinstance(action, str) and action.strip():
        return action
    return fallback


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
