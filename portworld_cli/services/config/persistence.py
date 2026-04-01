from __future__ import annotations

from portworld_cli.envfile import EnvWriteResult, parse_env_file, write_canonical_env
from portworld_cli.services.config.types import ConfigWriteOutcome
from portworld_cli.workspace.session import SecretReadiness, WorkspaceSession as ConfigSession
from portworld_cli.workspace.project_config import (
    ProjectConfig,
    build_env_overrides_from_project_config,
    write_project_config,
)
from portworld_shared.providers import (
    build_provider_requirement_diagnostics,
    compute_selected_provider_key_set,
    resolve_selected_providers,
)


def write_config_artifacts(
    session: ConfigSession,
    project_config: ProjectConfig,
    env_updates: dict[str, str],
) -> ConfigWriteOutcome:
    env_overrides = build_env_overrides_from_project_config(project_config)
    env_overrides.update(env_updates)
    write_project_config(session.workspace_paths.project_config_file, project_config)
    env_write_result: EnvWriteResult | None = None
    existing_env = session.existing_env
    if session.project_paths is not None and session.template is not None and session.existing_env is not None:
        env_write_result = write_canonical_env(
            session.project_paths.env_file,
            template=session.template,
            existing_env=session.existing_env,
            overrides=env_overrides,
        )
        existing_env = parse_env_file(session.project_paths.env_file, template=session.template)
    updated_session = ConfigSession(
        cli_context=session.cli_context,
        workspace_paths=session.workspace_paths,
        project_paths=session.project_paths,
        template=session.template,
        existing_env=existing_env,
        project_config=project_config,
        derived_from_legacy=False,
        configured_runtime_source=project_config.runtime_source,
        effective_runtime_source=project_config.runtime_source or session.effective_runtime_source,
        runtime_source_derived_from_legacy=False,
        remembered_deploy_state=session.remembered_deploy_state,
        remembered_deploy_state_target=session.remembered_deploy_state_target,
        workspace_resolution_source=session.workspace_resolution_source,
        active_workspace_root=session.active_workspace_root,
    )
    return ConfigWriteOutcome(
        project_config=project_config,
        secret_readiness=updated_session.secret_readiness(),
        env_write_result=env_write_result,
    )


def preview_secret_readiness(
    session: ConfigSession,
    project_config: ProjectConfig,
    env_updates: dict[str, str],
) -> SecretReadiness:
    return _secret_readiness_with_updates(session, project_config, env_updates)


def _secret_readiness_with_updates(
    session: ConfigSession,
    project_config: ProjectConfig,
    env_updates: dict[str, str],
) -> SecretReadiness:
    config_selection = build_env_overrides_from_project_config(project_config)
    selected = resolve_selected_providers(config_selection)
    key_set = compute_selected_provider_key_set(selected)

    if session.existing_env is None:
        key_presence = {key: None for key in key_set.required_secret_env_keys}
        config_key_presence = {key: None for key in key_set.required_non_secret_env_keys}
        return SecretReadiness(
            selected_realtime_provider=selected.realtime_provider,
            selected_vision_provider=selected.vision_provider,
            selected_search_provider=selected.search_provider,
            required_secret_keys=key_set.required_secret_env_keys,
            optional_secret_keys=key_set.optional_secret_env_keys,
            missing_required_secret_keys=(),
            required_config_keys=key_set.required_non_secret_env_keys,
            optional_config_keys=key_set.optional_non_secret_env_keys,
            missing_required_config_keys=(),
            key_presence=key_presence,
            config_key_presence=config_key_presence,
            bearer_token_present=None,
        )
    env_values = _build_effective_env_values(
        session,
        config_selection=config_selection,
        env_updates=env_updates,
    )
    diagnostics = build_provider_requirement_diagnostics(
        env_values,
        selected=selected,
    )
    key_presence = {
        key: diagnostics.secret_key_presence.get(key, False)
        for key in diagnostics.required_secret_env_keys
    }
    config_key_presence = {
        key: diagnostics.non_secret_key_presence.get(key, False)
        for key in diagnostics.required_non_secret_env_keys
    }

    return SecretReadiness(
        selected_realtime_provider=diagnostics.selected.realtime_provider,
        selected_vision_provider=diagnostics.selected.vision_provider,
        selected_search_provider=diagnostics.selected.search_provider,
        required_secret_keys=diagnostics.required_secret_env_keys,
        optional_secret_keys=diagnostics.optional_secret_env_keys,
        missing_required_secret_keys=diagnostics.missing_required_secret_env_keys,
        required_config_keys=diagnostics.required_non_secret_env_keys,
        optional_config_keys=diagnostics.optional_non_secret_env_keys,
        missing_required_config_keys=diagnostics.missing_required_non_secret_env_keys,
        key_presence=key_presence,
        config_key_presence=config_key_presence,
        bearer_token_present=bool((env_values.get("BACKEND_BEARER_TOKEN", "") or "").strip()),
    )


def _build_effective_env_values(
    session: ConfigSession,
    *,
    config_selection: dict[str, str],
    env_updates: dict[str, str],
) -> dict[str, str]:
    values = dict(session.existing_env.known_values)
    values.update(session.existing_env.preserved_overrides)
    values.update(config_selection)
    values.update(env_updates)
    return {key: str(value) for key, value in values.items()}
