from __future__ import annotations

import secrets
import re
from collections import OrderedDict

from portworld_cli.deploy.config import DeployStageError, DeployUsageError, ResolvedDeployConfig
from portworld_cli.gcp import GCPAdapters


def ensure_core_secrets(
    *,
    adapters: GCPAdapters,
    config: ResolvedDeployConfig,
    env_values: OrderedDict[str, str],
) -> tuple[list[str], str, str | None, str | None, str, str]:
    created_names: list[str] = []

    openai_secret_name = _ensure_secret_version(
        adapters=adapters,
        project_id=config.project_id,
        secret_name=_service_secret_name(config.service_name, "openai-api-key"),
        secret_value=_required_env_value(env_values, "OPENAI_API_KEY"),
        stage="secret_manager_setup",
    )
    created_names.append(openai_secret_name)

    vision_secret_name = None
    if _parse_bool_string(env_values.get("VISION_MEMORY_ENABLED", "false")):
        vision_secret_name = _ensure_secret_version(
            adapters=adapters,
            project_id=config.project_id,
            secret_name=_service_secret_name(config.service_name, "vision-provider-api-key"),
            secret_value=_required_env_value(env_values, "VISION_PROVIDER_API_KEY"),
            stage="secret_manager_setup",
        )
        created_names.append(vision_secret_name)

    tavily_secret_name = None
    tooling_enabled = _parse_bool_string(env_values.get("REALTIME_TOOLING_ENABLED", "false"))
    web_search_provider = (env_values.get("REALTIME_WEB_SEARCH_PROVIDER", "") or "").strip().lower()
    if tooling_enabled and web_search_provider == "tavily":
        tavily_secret_name = _ensure_secret_version(
            adapters=adapters,
            project_id=config.project_id,
            secret_name=_service_secret_name(config.service_name, "tavily-api-key"),
            secret_value=_required_env_value(env_values, "TAVILY_API_KEY"),
            stage="secret_manager_setup",
        )
        created_names.append(tavily_secret_name)

    bearer_secret_name = _service_secret_name(config.service_name, "backend-bearer-token")
    bearer_secret_result = adapters.secret_manager.get_secret(
        project_id=config.project_id,
        secret_name=bearer_secret_name,
    )
    if not bearer_secret_result.ok:
        raise DeployStageError(
            stage="secret_manager_setup",
            message=_gcp_error_message(bearer_secret_result.error, "Unable to inspect bearer-token secret."),
            action=_gcp_error_action(bearer_secret_result.error, "Verify Secret Manager access and rerun deploy."),
        )
    bearer_token = (env_values.get("BACKEND_BEARER_TOKEN", "") or "").strip()
    if bearer_secret_result.value is None:
        _ensure_secret_exists(
            adapters=adapters,
            project_id=config.project_id,
            secret_name=bearer_secret_name,
            stage="secret_manager_setup",
        )
        if not bearer_token:
            bearer_token = _generate_secure_token()
        _add_secret_version(
            adapters=adapters,
            project_id=config.project_id,
            secret_name=bearer_secret_name,
            secret_value=bearer_token,
            stage="secret_manager_setup",
        )
    elif bearer_token:
        _add_secret_version(
            adapters=adapters,
            project_id=config.project_id,
            secret_name=bearer_secret_name,
            secret_value=bearer_token,
            stage="secret_manager_setup",
        )
    created_names.append(bearer_secret_name)

    return (
        created_names,
        openai_secret_name,
        vision_secret_name,
        tavily_secret_name,
        bearer_secret_name,
        bearer_token or "__SECRET__",
    )


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


def _required_env_value(env_values: OrderedDict[str, str], key: str) -> str:
    value = (env_values.get(key, "") or "").strip()
    if value:
        return value
    raise DeployUsageError(f"{key} is required for Cloud Run deploy but is missing from backend/.env.")


def _service_secret_name(service_name: str, suffix: str) -> str:
    normalized_service = re.sub(r"[^a-z0-9-]+", "-", service_name.strip().lower()).strip("-")
    return f"{normalized_service}-{suffix}"


def _generate_secure_token(*, length: int = 32) -> str:
    return secrets.token_urlsafe(length)


def _parse_bool_string(raw_value: str) -> bool:
    return raw_value.strip().lower() in {"1", "true", "yes", "y", "on"}


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
