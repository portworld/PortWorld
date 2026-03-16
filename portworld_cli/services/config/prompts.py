from __future__ import annotations

import secrets

import click

from portworld_cli.context import CLIContext
from portworld_cli.workspace.project_config import DEFAULT_BACKEND_PROFILE
from portworld_cli.services.config.errors import ConfigUsageError, ConfigValidationError
from portworld_cli.services.config.types import SecurityEditOptions


def resolve_toggle(
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


def resolve_secret_value(
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
            raise ConfigValidationError(f"{label} is required.")
        return value

    current_value = existing_value.strip()
    if cli_context.non_interactive:
        if required and not current_value:
            raise ConfigValidationError(f"{label} is required in non-interactive mode.")
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


def resolve_bearer_token(
    cli_context: CLIContext,
    *,
    existing_value: str,
    explicit_value: str | None,
    generate: bool,
    clear: bool,
) -> str:
    if explicit_value is not None and (generate or clear):
        raise ConfigUsageError(
            "Use only one of --bearer-token, --generate-bearer-token, or --clear-bearer-token."
        )
    if generate and clear:
        raise ConfigUsageError(
            "Use only one of --generate-bearer-token or --clear-bearer-token."
        )
    if explicit_value is not None:
        value = explicit_value.strip()
        if not value:
            raise ConfigValidationError("Bearer token cannot be empty. Use --clear-bearer-token instead.")
        return value
    if clear:
        return ""
    if generate:
        return secrets.token_hex(32)

    current_value = existing_value.strip()
    if cli_context.non_interactive:
        return current_value

    if current_value:
        action = click.prompt(
            "Bearer token action",
            type=click.Choice(["keep", "generate", "replace", "clear"]),
            default="keep",
            show_default=True,
        )
        if action == "keep":
            return current_value
        if action == "generate":
            return secrets.token_hex(32)
        if action == "clear":
            return ""
        return resolve_secret_value(
            cli_context,
            label="Bearer token",
            existing_value=current_value,
            explicit_value=None,
            required=True,
        )

    should_generate = click.confirm(
        "Generate a local bearer token for development?",
        default=False,
        show_default=True,
    )
    if should_generate:
        return secrets.token_hex(32)
    return resolve_secret_value(
        cli_context,
        label="Bearer token (optional)",
        existing_value="",
        explicit_value=None,
        required=False,
    )


def resolve_choice_value(
    cli_context: CLIContext,
    *,
    prompt: str,
    current_value: str,
    explicit_value: str | None,
    choices: tuple[str, ...],
) -> str:
    if explicit_value is not None:
        normalized = explicit_value.strip().lower()
        if normalized not in choices:
            allowed = ", ".join(choices)
            raise ConfigValidationError(f"{prompt} must be one of: {allowed}.")
        return normalized
    if cli_context.non_interactive:
        return current_value
    return click.prompt(
        prompt,
        type=click.Choice(choices),
        default=current_value,
        show_default=True,
    )


def resolve_csv_value(
    cli_context: CLIContext,
    *,
    prompt: str,
    current_values: tuple[str, ...],
    explicit_value: str | None,
) -> tuple[str, ...]:
    if explicit_value is not None:
        values = _parse_csv_tuple(explicit_value)
        if not values:
            raise ConfigValidationError(f"{prompt} cannot be empty.")
        return values
    if cli_context.non_interactive:
        return current_values
    current_text = ",".join(current_values)
    response = click.prompt(
        prompt,
        default=current_text,
        show_default=True,
    )
    values = _parse_csv_tuple(response)
    if not values:
        raise ConfigValidationError(f"{prompt} cannot be empty.")
    return values


def resolve_required_text_value(
    cli_context: CLIContext,
    *,
    prompt: str,
    current_value: str,
    explicit_value: str | None,
) -> str:
    if explicit_value is not None:
        value = explicit_value.strip()
        if not value:
            raise ConfigValidationError(f"{prompt} is required.")
        return value
    if cli_context.non_interactive:
        if not current_value.strip():
            raise ConfigValidationError(f"{prompt} is required in non-interactive mode.")
        return current_value.strip()
    response = click.prompt(prompt, default=current_value, show_default=True)
    value = response.strip()
    if not value:
        raise ConfigValidationError(f"{prompt} is required.")
    return value


def resolve_optional_text_value(
    cli_context: CLIContext,
    *,
    prompt: str,
    current_value: str | None,
    explicit_value: str | None,
) -> str | None:
    if explicit_value is not None:
        value = explicit_value.strip()
        return value or None
    if cli_context.non_interactive:
        return current_value
    response = click.prompt(
        prompt,
        default=current_value or "",
        show_default=bool(current_value),
    )
    value = response.strip()
    return value or None


def resolve_int_value(
    cli_context: CLIContext,
    *,
    prompt: str,
    current_value: int,
    explicit_value: int | None,
) -> int:
    if explicit_value is not None:
        return explicit_value
    if cli_context.non_interactive:
        return current_value
    return int(
        click.prompt(
            prompt,
            type=int,
            default=current_value,
            show_default=True,
        )
    )


def validate_security_flag_conflicts(options: SecurityEditOptions) -> None:
    if options.generate_bearer_token and options.clear_bearer_token:
        raise ConfigUsageError(
            "Use only one of --generate-bearer-token or --clear-bearer-token."
        )


def normalize_backend_profile(value: str | None) -> str:
    normalized = (value or DEFAULT_BACKEND_PROFILE).strip().lower()
    if normalized in {"prod", "production"}:
        return "production"
    return "development"


def presence_label(is_present: bool | None) -> str:
    if is_present is None:
        return "unknown"
    return "present" if is_present else "missing"


def required_presence_label(required: bool, present: bool | None) -> str:
    if not required:
        return "not_required"
    return "present" if present else "missing"


def _parse_csv_tuple(raw_value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw_value.split(",") if part.strip())
