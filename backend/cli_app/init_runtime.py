from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import secrets

import click

from backend.cli_app.context import CLIContext
from backend.cli_app.envfile import (
    EnvFileParseError,
    EnvTemplateError,
    ParsedEnvFile,
    load_env_template,
    parse_env_file,
    write_canonical_env,
)
from backend.cli_app.output import CommandResult, DiagnosticCheck
from backend.cli_app.paths import ProjectRootResolutionError


COMMAND_NAME = "portworld init"
FIXED_REALTIME_PROVIDER = "openai"
FIXED_VISION_PROVIDER = "mistral"
FIXED_WEB_SEARCH_PROVIDER = "tavily"


class InitUsageError(RuntimeError):
    pass


class InitValidationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class InitOptions:
    force: bool
    with_vision: bool
    without_vision: bool
    with_tooling: bool
    without_tooling: bool
    openai_api_key: str | None
    vision_provider_api_key: str | None
    tavily_api_key: str | None


@dataclass(frozen=True, slots=True)
class InitSelections:
    openai_api_key: str
    vision_enabled: bool
    vision_provider_api_key: str
    tooling_enabled: bool
    tavily_api_key: str
    bearer_token: str
    missing_optional_integrations: tuple[str, ...]


def run_init(cli_context: CLIContext, options: InitOptions) -> CommandResult:
    try:
        project_paths = cli_context.resolve_project_paths()
        template = load_env_template(project_paths.env_example_file)
        existing_env = parse_env_file(project_paths.env_file, template=template)
        _validate_flag_conflicts(options)
        _confirm_overwrite_if_needed(
            cli_context,
            env_path=project_paths.env_file,
            force=options.force,
        )
        selections = _collect_selections(
            cli_context,
            existing_env=existing_env,
            options=options,
        )
        overrides = _build_env_overrides(existing_env=existing_env, selections=selections)
        write_result = write_canonical_env(
            project_paths.env_file,
            template=template,
            existing_env=existing_env,
            overrides=overrides,
        )
    except ProjectRootResolutionError as exc:
        return _repo_resolution_failure(exc)
    except (EnvTemplateError, EnvFileParseError, InitUsageError) as exc:
        return _failure_result(exc, exit_code=2)
    except InitValidationError as exc:
        return _failure_result(exc, exit_code=2)
    except click.Abort:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message="Aborted; backend/.env was not modified.",
            data={
                "status": "aborted",
                "error_type": "Abort",
            },
            exit_code=1,
        )
    except Exception as exc:
        return _failure_result(exc, exit_code=1)

    checks: list[DiagnosticCheck] = []
    if selections.missing_optional_integrations:
        for integration in selections.missing_optional_integrations:
            checks.append(
                DiagnosticCheck(
                    id=f"missing-{integration}",
                    status="warn",
                    message=f"{integration} is not configured yet.",
                    action="Run `portworld init` again to add the missing optional credential.",
                )
            )

    features = {
        "vision_memory": selections.vision_enabled,
        "realtime_tooling": selections.tooling_enabled,
        "web_search_provider": FIXED_WEB_SEARCH_PROVIDER if selections.tooling_enabled else None,
    }
    message = _build_success_message(
        env_path=write_result.env_path,
        backup_path=write_result.backup_path,
        features=features,
        missing_optional_integrations=selections.missing_optional_integrations,
    )
    return CommandResult(
        ok=True,
        command=COMMAND_NAME,
        message=message,
        data={
            "project_root": str(project_paths.project_root),
            "env_path": str(write_result.env_path),
            "backup_path": str(write_result.backup_path) if write_result.backup_path else None,
            "features": features,
        },
        checks=tuple(checks),
        exit_code=0,
    )


def _validate_flag_conflicts(options: InitOptions) -> None:
    if options.with_vision and options.without_vision:
        raise InitUsageError("Use only one of --with-vision or --without-vision.")
    if options.with_tooling and options.without_tooling:
        raise InitUsageError("Use only one of --with-tooling or --without-tooling.")


def _confirm_overwrite_if_needed(cli_context: CLIContext, *, env_path: Path, force: bool) -> None:
    if not env_path.exists():
        return
    if force or cli_context.yes:
        return
    if cli_context.non_interactive:
        raise InitUsageError(
            "backend/.env already exists. Re-run with --force or --yes to overwrite it in non-interactive mode."
        )
    confirmed = click.confirm(
        "backend/.env already exists. Rewrite it in canonical PortWorld order?",
        default=True,
        show_default=True,
    )
    if not confirmed:
        raise click.Abort()


def _collect_selections(
    cli_context: CLIContext,
    *,
    existing_env: ParsedEnvFile,
    options: InitOptions,
) -> InitSelections:
    openai_api_key = _resolve_secret_value(
        cli_context,
        label="OpenAI API key",
        existing_value=existing_env.known_values.get("OPENAI_API_KEY", ""),
        explicit_value=options.openai_api_key,
        required=True,
    )

    current_vision_enabled = _parse_bool_string(
        existing_env.known_values.get("VISION_MEMORY_ENABLED", "false")
    )
    vision_enabled = _resolve_toggle(
        cli_context,
        prompt="Enable visual memory?",
        current_value=current_vision_enabled,
        explicit_enable=options.with_vision,
        explicit_disable=options.without_vision,
    )

    vision_provider_api_key = ""
    if vision_enabled:
        if not cli_context.non_interactive:
            click.echo(f"Visual memory provider: {FIXED_VISION_PROVIDER}")
        vision_provider_api_key = _resolve_secret_value(
            cli_context,
            label="Vision provider API key",
            existing_value=existing_env.known_values.get("VISION_PROVIDER_API_KEY", ""),
            explicit_value=options.vision_provider_api_key,
            required=True,
        )

    current_tooling_enabled = _parse_bool_string(
        existing_env.known_values.get("REALTIME_TOOLING_ENABLED", "false")
    )
    tooling_enabled = _resolve_toggle(
        cli_context,
        prompt="Enable realtime tooling?",
        current_value=current_tooling_enabled,
        explicit_enable=options.with_tooling,
        explicit_disable=options.without_tooling,
    )

    tavily_api_key = ""
    missing_optional_integrations: list[str] = []
    if tooling_enabled:
        if not cli_context.non_interactive:
            click.echo(f"Web search provider: {FIXED_WEB_SEARCH_PROVIDER}")
        tavily_api_key = _resolve_secret_value(
            cli_context,
            label="Tavily API key (optional)",
            existing_value=existing_env.known_values.get("TAVILY_API_KEY", ""),
            explicit_value=options.tavily_api_key,
            required=False,
        )
        if not tavily_api_key:
            missing_optional_integrations.append("tavily-api-key")

    bearer_token = _resolve_bearer_token(
        cli_context,
        existing_value=existing_env.known_values.get("BACKEND_BEARER_TOKEN", ""),
    )

    return InitSelections(
        openai_api_key=openai_api_key,
        vision_enabled=vision_enabled,
        vision_provider_api_key=vision_provider_api_key,
        tooling_enabled=tooling_enabled,
        tavily_api_key=tavily_api_key,
        bearer_token=bearer_token,
        missing_optional_integrations=tuple(missing_optional_integrations),
    )


def _resolve_toggle(
    cli_context: CLIContext,
    *,
    prompt: str,
    current_value: bool,
    explicit_enable: bool,
    explicit_disable: bool,
) -> bool:
    if explicit_enable:
        return True
    if explicit_disable:
        return False
    if cli_context.non_interactive:
        return current_value
    return bool(click.confirm(prompt, default=current_value, show_default=True))


def _resolve_secret_value(
    cli_context: CLIContext,
    *,
    label: str,
    existing_value: str,
    explicit_value: str | None,
    required: bool,
) -> str:
    if explicit_value is not None:
        value = explicit_value.strip()
        if required and not value:
            raise InitValidationError(f"{label} is required.")
        return value

    current_value = existing_value.strip()
    if cli_context.non_interactive:
        if required and not current_value:
            raise InitValidationError(f"{label} is required in non-interactive mode.")
        return current_value

    if current_value:
        click.echo(f"{label}: existing value detected.")

    while True:
        prompt_text = (
            f"{label} (press Enter to keep the existing value)"
            if current_value
            else label
        )
        response = click.prompt(
            prompt_text,
            default="",
            show_default=False,
            hide_input=True,
        ).strip()
        if response:
            return response
        if current_value:
            return current_value
        if not required:
            return ""
        click.echo(f"{label} is required.", err=True)


def _resolve_bearer_token(cli_context: CLIContext, *, existing_value: str) -> str:
    current_value = existing_value.strip()
    if cli_context.non_interactive:
        return current_value

    prompt = (
        "Generate a new local bearer token?"
        if current_value
        else "Generate a local bearer token for development?"
    )
    should_generate = click.confirm(prompt, default=False, show_default=True)
    if should_generate:
        return secrets.token_hex(32)
    return current_value


def _build_env_overrides(
    *,
    existing_env: ParsedEnvFile,
    selections: InitSelections,
) -> dict[str, str]:
    existing_token = existing_env.known_values.get("BACKEND_BEARER_TOKEN", "")
    bearer_token = selections.bearer_token if selections.bearer_token or not existing_token else existing_token
    return {
        "REALTIME_PROVIDER": FIXED_REALTIME_PROVIDER,
        "OPENAI_API_KEY": selections.openai_api_key,
        "VISION_MEMORY_ENABLED": _bool_env_value(selections.vision_enabled),
        "VISION_MEMORY_PROVIDER": FIXED_VISION_PROVIDER,
        "VISION_PROVIDER_API_KEY": selections.vision_provider_api_key if selections.vision_enabled else "",
        "REALTIME_TOOLING_ENABLED": _bool_env_value(selections.tooling_enabled),
        "REALTIME_WEB_SEARCH_PROVIDER": FIXED_WEB_SEARCH_PROVIDER,
        "TAVILY_API_KEY": selections.tavily_api_key if selections.tooling_enabled else "",
        "BACKEND_BEARER_TOKEN": bearer_token,
    }


def _build_success_message(
    *,
    env_path: Path,
    backup_path: Path | None,
    features: dict[str, object | None],
    missing_optional_integrations: tuple[str, ...],
) -> str:
    lines = [
        f"env_path: {env_path}",
        f"vision_memory: {'yes' if features['vision_memory'] else 'no'}",
        f"realtime_tooling: {'yes' if features['realtime_tooling'] else 'no'}",
    ]
    web_search_provider = features.get("web_search_provider")
    if web_search_provider is not None:
        lines.append(f"web_search_provider: {web_search_provider}")
    if backup_path is not None:
        lines.append(f"backup_path: {backup_path}")
    if missing_optional_integrations:
        lines.append(
            "missing_optional_integrations: " + ", ".join(missing_optional_integrations)
        )
    lines.extend(
        [
            "next: portworld doctor",
            "next: docker compose up --build",
            "next: portworld deploy gcp-cloud-run",
        ]
    )
    return "\n".join(lines)


def _parse_bool_string(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _bool_env_value(value: bool) -> str:
    return "true" if value else "false"


def _repo_resolution_failure(exc: ProjectRootResolutionError) -> CommandResult:
    return CommandResult(
        ok=False,
        command=COMMAND_NAME,
        message=str(exc),
        data={
            "status": "error",
            "error_type": type(exc).__name__,
        },
        checks=(
            DiagnosticCheck(
                id="project-root",
                status="fail",
                message=str(exc),
                action="Run from a PortWorld repo checkout or pass --project-root.",
            ),
        ),
        exit_code=1,
    )


def _failure_result(exc: Exception, *, exit_code: int) -> CommandResult:
    return CommandResult(
        ok=False,
        command=COMMAND_NAME,
        message=str(exc),
        data={
            "status": "error",
            "error_type": type(exc).__name__,
        },
        exit_code=exit_code,
    )
