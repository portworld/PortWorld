from __future__ import annotations

import secrets
import re
from collections import OrderedDict

from backend.core.provider_requirements import (
    build_provider_requirement_diagnostics,
    compute_selected_provider_key_set,
    resolve_effective_env_value,
    resolve_selected_providers,
)
from portworld_cli.deploy.config import DeployStageError, DeployUsageError, ResolvedDeployConfig
from portworld_cli.gcp import GCPAdapters


def ensure_core_secrets(
    *,
    adapters: GCPAdapters,
    config: ResolvedDeployConfig,
    env_values: OrderedDict[str, str],
) -> tuple[list[str], dict[str, str], dict[str, str], str, str]:
    created_names: list[str] = []
    provider_secret_names: dict[str, str] = {}
    provider_secret_values: dict[str, str] = {}
    selected = resolve_selected_providers(env_values)
    key_set = compute_selected_provider_key_set(selected)
    requirement_diagnostics = build_provider_requirement_diagnostics(
        env_values,
        selected=selected,
    )
    if requirement_diagnostics.missing_required_non_secret_env_keys:
        missing_keys = ", ".join(requirement_diagnostics.missing_required_non_secret_env_keys)
        raise DeployUsageError(
            "Missing required non-secret provider configuration for Cloud Run deploy: "
            f"{missing_keys}. Set these keys in backend/.env for the selected provider configuration."
        )

    for entry in key_set.entries:
        for env_key in entry.secret_binding.required_env_keys:
            if env_key in provider_secret_names:
                continue
            secret_value, _ = resolve_effective_env_value(
                values=env_values,
                provider_kind=entry.kind,
                provider_id=entry.provider_id,
                env_key=env_key,
            )
            if not secret_value:
                raise DeployUsageError(
                    f"{env_key} is required for Cloud Run deploy but is missing from backend/.env "
                    f"for selected provider {entry.kind}:{entry.provider_id}."
                )
            secret_name = _ensure_secret_version(
                adapters=adapters,
                project_id=config.project_id,
                secret_name=_service_secret_name(config.service_name, _env_key_secret_suffix(env_key)),
                secret_value=secret_value,
                stage="secret_manager_setup",
            )
            created_names.append(secret_name)
            provider_secret_names[env_key] = secret_name
            provider_secret_values[env_key] = secret_value

        for env_key in entry.secret_binding.optional_env_keys:
            if env_key in provider_secret_names:
                continue
            secret_value, _ = resolve_effective_env_value(
                values=env_values,
                provider_kind=entry.kind,
                provider_id=entry.provider_id,
                env_key=env_key,
            )
            if not secret_value:
                continue
            secret_name = _ensure_secret_version(
                adapters=adapters,
                project_id=config.project_id,
                secret_name=_service_secret_name(config.service_name, _env_key_secret_suffix(env_key)),
                secret_value=secret_value,
                stage="secret_manager_setup",
            )
            created_names.append(secret_name)
            provider_secret_names[env_key] = secret_name
            provider_secret_values[env_key] = secret_value

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
        provider_secret_names,
        provider_secret_values,
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


def _service_secret_name(service_name: str, suffix: str) -> str:
    normalized_service = re.sub(r"[^a-z0-9-]+", "-", service_name.strip().lower()).strip("-")
    return f"{normalized_service}-{suffix}"


def _generate_secure_token(*, length: int = 32) -> str:
    return secrets.token_urlsafe(length)


def _env_key_secret_suffix(env_key: str) -> str:
    return env_key.strip().lower().replace("_", "-")


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
