from __future__ import annotations

from backend.core.provider_requirements import (
    PROVIDER_KIND_REALTIME,
    PROVIDER_KIND_SEARCH,
    PROVIDER_KIND_VISION,
    compute_selected_provider_key_set,
    get_provider_requirement,
    resolve_effective_env_value,
    supported_provider_ids,
)
from portworld_cli.workspace.project_config import (
    ProjectConfig,
    ToolingConfig,
    VisionProviderConfig,
)
from portworld_cli.providers.types import ProviderEditOptions, ProviderSectionResult
from portworld_cli.workspace.session import WorkspaceSession as ConfigSession


def collect_provider_section(
    session: ConfigSession,
    options: ProviderEditOptions,
) -> ProviderSectionResult:
    from portworld_cli.services.config.prompts import (
        resolve_choice_value,
        resolve_required_text_value,
        resolve_secret_value,
        resolve_toggle,
    )

    _validate_provider_flag_conflicts(options)

    existing_values = _existing_env_values(session)

    realtime_provider = resolve_choice_value(
        session.cli_context,
        prompt="Realtime provider",
        current_value=_default_choice(
            session.project_config.providers.realtime.provider,
            choices=supported_provider_ids(PROVIDER_KIND_REALTIME),
        ),
        explicit_value=options.realtime_provider,
        choices=supported_provider_ids(PROVIDER_KIND_REALTIME),
    )

    vision_enabled = resolve_toggle(
        session.cli_context,
        prompt="Enable visual memory?",
        current_value=session.project_config.providers.vision.enabled,
        explicit_enable=options.with_vision,
        explicit_disable=options.without_vision,
    )
    vision_provider = _default_choice(
        session.project_config.providers.vision.provider,
        choices=supported_provider_ids(PROVIDER_KIND_VISION),
    )
    if vision_enabled:
        vision_provider = resolve_choice_value(
            session.cli_context,
            prompt="Vision provider",
            current_value=vision_provider,
            explicit_value=options.vision_provider,
            choices=supported_provider_ids(PROVIDER_KIND_VISION),
        )
    _validate_provider_toggle_dependencies(
        vision_enabled=vision_enabled,
        tooling_enabled=session.project_config.providers.tooling.enabled,
        options=options,
        check_tooling=False,
    )

    tooling_enabled = resolve_toggle(
        session.cli_context,
        prompt="Enable realtime tooling?",
        current_value=session.project_config.providers.tooling.enabled,
        explicit_enable=options.with_tooling,
        explicit_disable=options.without_tooling,
    )
    search_provider = _default_choice(
        session.project_config.providers.tooling.web_search_provider,
        choices=supported_provider_ids(PROVIDER_KIND_SEARCH),
    )
    if tooling_enabled:
        search_provider = resolve_choice_value(
            session.cli_context,
            prompt="Realtime web-search provider",
            current_value=search_provider,
            explicit_value=options.search_provider,
            choices=supported_provider_ids(PROVIDER_KIND_SEARCH),
        )
    _validate_provider_toggle_dependencies(
        vision_enabled=vision_enabled,
        tooling_enabled=tooling_enabled,
        options=options,
        check_tooling=True,
    )

    selection_inputs = {
        "REALTIME_PROVIDER": realtime_provider,
        "VISION_MEMORY_ENABLED": "true" if vision_enabled else "false",
        "VISION_MEMORY_PROVIDER": vision_provider,
        "REALTIME_TOOLING_ENABLED": "true" if tooling_enabled else "false",
        "REALTIME_WEB_SEARCH_PROVIDER": search_provider,
    }
    key_set = compute_selected_provider_key_set(
        selected=_selected_providers(selection_inputs)
    )

    env_updates: dict[str, str] = {}
    for entry in key_set.entries:
        for env_key in entry.required_secret_env_keys:
            existing_value, _ = resolve_effective_env_value(
                values=existing_values,
                provider_kind=entry.kind,
                provider_id=entry.provider_id,
                env_key=env_key,
            )
            explicit_value = _explicit_secret_value(
                env_key=env_key,
                selected_search_provider=search_provider,
                options=options,
            )
            label = f"{env_key} ({entry.display_name})"
            env_updates[env_key] = resolve_secret_value(
                session.cli_context,
                label=label,
                existing_value=existing_value or "",
                explicit_value=explicit_value,
                required=True,
            )
        for env_key in entry.required_non_secret_env_keys:
            existing_value, _ = resolve_effective_env_value(
                values=existing_values,
                provider_kind=entry.kind,
                provider_id=entry.provider_id,
                env_key=env_key,
            )
            label = f"{env_key} ({entry.display_name})"
            env_updates[env_key] = resolve_required_text_value(
                session.cli_context,
                prompt=label,
                current_value=existing_value or "",
                explicit_value=None,
            )

    return ProviderSectionResult(
        realtime_provider=realtime_provider,
        vision_enabled=vision_enabled,
        vision_provider=vision_provider,
        tooling_enabled=tooling_enabled,
        search_provider=search_provider,
        env_updates=env_updates,
    )


def apply_provider_section(
    project_config: ProjectConfig,
    result: ProviderSectionResult,
) -> tuple[ProjectConfig, dict[str, str]]:
    updated_project_config = ProjectConfig(
        schema_version=project_config.schema_version,
        project_mode=project_config.project_mode,
        runtime_source=project_config.runtime_source,
        cloud_provider=project_config.cloud_provider,
        providers=type(project_config.providers)(
            realtime=type(project_config.providers.realtime)(
                provider=result.realtime_provider,
            ),
            vision=VisionProviderConfig(
                enabled=result.vision_enabled,
                provider=result.vision_provider,
            ),
            tooling=ToolingConfig(
                enabled=result.tooling_enabled,
                web_search_provider=result.search_provider,
            ),
        ),
        security=project_config.security,
        deploy=project_config.deploy,
    )

    env_updates: dict[str, str] = {
        "REALTIME_PROVIDER": result.realtime_provider,
        "VISION_MEMORY_ENABLED": "true" if result.vision_enabled else "false",
        "VISION_MEMORY_PROVIDER": result.vision_provider,
        "REALTIME_TOOLING_ENABLED": "true" if result.tooling_enabled else "false",
        "REALTIME_WEB_SEARCH_PROVIDER": result.search_provider,
    }
    env_updates.update(result.env_updates)

    if not result.vision_enabled:
        for key in _required_secret_keys_for_provider(PROVIDER_KIND_VISION, result.vision_provider):
            env_updates.setdefault(key, "")
        for key in _required_non_secret_keys_for_provider(PROVIDER_KIND_VISION, result.vision_provider):
            env_updates.setdefault(key, "")
    if not result.tooling_enabled:
        for key in _required_secret_keys_for_provider(PROVIDER_KIND_SEARCH, result.search_provider):
            env_updates.setdefault(key, "")
        for key in _required_non_secret_keys_for_provider(PROVIDER_KIND_SEARCH, result.search_provider):
            env_updates.setdefault(key, "")

    return updated_project_config, env_updates


def _validate_provider_flag_conflicts(options: ProviderEditOptions) -> None:
    from portworld_cli.services.config.errors import ConfigUsageError

    if options.with_vision and options.without_vision:
        raise ConfigUsageError("Use only one of --with-vision or --without-vision.")
    if options.with_tooling and options.without_tooling:
        raise ConfigUsageError("Use only one of --with-tooling or --without-tooling.")
    if (options.openai_api_key or "").strip():
        raise ConfigUsageError("--openai-api-key has been removed. Use --realtime-api-key.")
    if (options.vision_provider_api_key or "").strip():
        raise ConfigUsageError("--vision-provider-api-key has been removed. Use --vision-api-key.")
    if (options.tavily_api_key or "").strip():
        raise ConfigUsageError("--tavily-api-key has been removed. Use --search-api-key.")


def _validate_provider_toggle_dependencies(
    *,
    vision_enabled: bool,
    tooling_enabled: bool,
    options: ProviderEditOptions,
    check_tooling: bool,
) -> None:
    from portworld_cli.services.config.errors import ConfigUsageError

    if not check_tooling:
        if options.vision_provider is not None and not vision_enabled:
            raise ConfigUsageError("--vision-provider requires visual memory to be enabled.")
        if (options.vision_api_key or "").strip() and not vision_enabled:
            raise ConfigUsageError("--vision-api-key requires visual memory to be enabled.")
        return

    if options.search_provider is not None and not tooling_enabled:
        raise ConfigUsageError("--search-provider requires realtime tooling to be enabled.")
    if (options.search_api_key or "").strip() and not tooling_enabled:
        raise ConfigUsageError("--search-api-key requires realtime tooling to be enabled.")


def _selected_providers(selection_inputs: dict[str, str]):
    from backend.core.provider_requirements import resolve_selected_providers

    return resolve_selected_providers(selection_inputs)


def _existing_env_values(session: ConfigSession) -> dict[str, str]:
    return session.merged_env_values()


def _default_choice(current: str, *, choices: tuple[str, ...]) -> str:
    normalized = (current or "").strip().lower()
    if normalized in choices:
        return normalized
    return choices[0]


def _required_secret_keys_for_provider(kind: str, provider_id: str) -> tuple[str, ...]:
    return get_provider_requirement(kind=kind, provider_id=provider_id).required_secret_env_keys


def _required_non_secret_keys_for_provider(kind: str, provider_id: str) -> tuple[str, ...]:
    return get_provider_requirement(
        kind=kind,
        provider_id=provider_id,
    ).required_non_secret_env_keys


def _explicit_secret_value(
    *,
    env_key: str,
    selected_search_provider: str,
    options: ProviderEditOptions,
) -> str | None:
    if env_key == "OPENAI_API_KEY":
        return options.realtime_api_key
    if env_key == "GEMINI_LIVE_API_KEY":
        return options.realtime_api_key
    if env_key == "TAVILY_API_KEY" and selected_search_provider == "tavily":
        return options.search_api_key
    if env_key.startswith("VISION_") and env_key.endswith("_API_KEY"):
        return options.vision_api_key

    return None
