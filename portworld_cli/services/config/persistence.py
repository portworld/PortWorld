from __future__ import annotations

from portworld_cli.envfile import EnvWriteResult, parse_env_file, write_canonical_env
from portworld_cli.workspace.project_config import (
    ProjectConfig,
    build_env_overrides_from_project_config,
    write_project_config,
)
from portworld_cli.services.config.types import ConfigWriteOutcome
from portworld_cli.workspace.session import SecretReadiness, WorkspaceSession as ConfigSession


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
    if session.existing_env is None:
        return SecretReadiness(
            openai_api_key_present=None,
            vision_provider_secret_required=project_config.providers.vision.enabled,
            vision_provider_api_key_present=None,
            tavily_secret_required=project_config.providers.tooling.enabled,
            tavily_api_key_present=None,
            bearer_token_present=None,
        )
    known_values = dict(session.existing_env.known_values)
    known_values.update(env_updates)

    def _known(key: str) -> str:
        return str(known_values.get(key, "")).strip()

    vision_required = project_config.providers.vision.enabled
    vision_present: bool | None = None
    if vision_required:
        vision_present = bool(
            _known("VISION_PROVIDER_API_KEY")
            or session.existing_env.legacy_alias_values.get("MISTRAL_API_KEY", "").strip()
        )

    tavily_required = project_config.providers.tooling.enabled
    tavily_present: bool | None = None
    if tavily_required:
        tavily_present = bool(_known("TAVILY_API_KEY"))

    return SecretReadiness(
        openai_api_key_present=bool(_known("OPENAI_API_KEY")),
        vision_provider_secret_required=vision_required,
        vision_provider_api_key_present=vision_present,
        tavily_secret_required=tavily_required,
        tavily_api_key_present=tavily_present,
        bearer_token_present=bool(_known("BACKEND_BEARER_TOKEN")),
    )
