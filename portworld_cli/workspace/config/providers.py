from __future__ import annotations

import click

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
    from portworld_cli.services.config.prompts import resolve_secret_value, resolve_toggle

    _validate_provider_flag_conflicts(options)

    existing_env = session.existing_env
    openai_api_key = resolve_secret_value(
        session.cli_context,
        label="OpenAI API key",
        existing_value="" if existing_env is None else existing_env.known_values.get("OPENAI_API_KEY", ""),
        explicit_value=options.openai_api_key,
        required=True,
    )
    vision_enabled = resolve_toggle(
        session.cli_context,
        prompt="Enable visual memory?",
        current_value=session.project_config.providers.vision.enabled,
        explicit_enable=options.with_vision,
        explicit_disable=options.without_vision,
    )
    vision_provider_api_key = ""
    if vision_enabled:
        if not session.cli_context.non_interactive:
            click.echo(
                f"Visual memory provider: {session.project_config.providers.vision.provider}"
            )
        vision_provider_api_key = resolve_secret_value(
            session.cli_context,
            label="Vision provider API key",
            existing_value=(
                ""
                if existing_env is None
                else (
                    existing_env.known_values.get("VISION_PROVIDER_API_KEY", "")
                    or existing_env.legacy_alias_values.get("MISTRAL_API_KEY", "")
                )
            ),
            explicit_value=options.vision_provider_api_key,
            required=True,
        )

    tooling_enabled = resolve_toggle(
        session.cli_context,
        prompt="Enable realtime tooling?",
        current_value=session.project_config.providers.tooling.enabled,
        explicit_enable=options.with_tooling,
        explicit_disable=options.without_tooling,
    )
    tavily_api_key = ""
    if tooling_enabled:
        if not session.cli_context.non_interactive:
            click.echo(
                "Web search provider: "
                f"{session.project_config.providers.tooling.web_search_provider}"
            )
        tavily_api_key = resolve_secret_value(
            session.cli_context,
            label="Tavily API key (optional)",
            existing_value="" if existing_env is None else existing_env.known_values.get("TAVILY_API_KEY", ""),
            explicit_value=options.tavily_api_key,
            required=False,
        )

    return ProviderSectionResult(
        vision_enabled=vision_enabled,
        tooling_enabled=tooling_enabled,
        openai_api_key=openai_api_key,
        vision_provider_api_key=vision_provider_api_key,
        tavily_api_key=tavily_api_key,
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
            realtime=project_config.providers.realtime,
            vision=VisionProviderConfig(
                enabled=result.vision_enabled,
                provider=project_config.providers.vision.provider,
            ),
            tooling=ToolingConfig(
                enabled=result.tooling_enabled,
                web_search_provider=project_config.providers.tooling.web_search_provider,
            ),
        ),
        security=project_config.security,
        deploy=project_config.deploy,
    )
    env_updates = {
        "OPENAI_API_KEY": result.openai_api_key,
        "VISION_PROVIDER_API_KEY": result.vision_provider_api_key if result.vision_enabled else "",
        "TAVILY_API_KEY": result.tavily_api_key if result.tooling_enabled else "",
    }
    return updated_project_config, env_updates


def _validate_provider_flag_conflicts(options: ProviderEditOptions) -> None:
    from portworld_cli.services.config.errors import ConfigUsageError

    if options.with_vision and options.without_vision:
        raise ConfigUsageError("Use only one of --with-vision or --without-vision.")
    if options.with_tooling and options.without_tooling:
        raise ConfigUsageError("Use only one of --with-tooling or --without-tooling.")
