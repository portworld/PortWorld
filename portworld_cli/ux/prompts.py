from __future__ import annotations

import sys

import click

from portworld_cli.context import CLIContext

try:
    from InquirerPy import inquirer  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional dependency fallback
    inquirer = None


def prompt_choice(
    cli_context: CLIContext,
    *,
    message: str,
    choices: tuple[str, ...],
    default: str,
    labels: dict[str, str] | None = None,
) -> str:
    if _should_use_inquirer(cli_context):
        prompt_choices = [
            {"name": labels.get(choice, choice) if labels else choice, "value": choice}
            for choice in choices
        ]
        try:
            value = inquirer.select(  # type: ignore[union-attr]
                message=message,
                choices=prompt_choices,
                default=default,
                cycle=True,
            ).execute()
        except KeyboardInterrupt as exc:
            raise click.Abort() from exc
        return str(value).strip().lower()

    return str(
        click.prompt(
            message,
            type=click.Choice(choices),
            default=default,
            show_default=True,
        )
    ).strip().lower()


def prompt_confirm(
    cli_context: CLIContext,
    *,
    message: str,
    default: bool,
) -> bool:
    if _should_use_inquirer(cli_context):
        try:
            return bool(
                inquirer.confirm(  # type: ignore[union-attr]
                    message=message,
                    default=default,
                ).execute()
            )
        except KeyboardInterrupt as exc:
            raise click.Abort() from exc
    return bool(click.confirm(message, default=default, show_default=True))


def prompt_text(
    cli_context: CLIContext,
    *,
    message: str,
    default: str = "",
    show_default: bool = True,
    secret: bool = False,
) -> str:
    if _should_use_inquirer(cli_context):
        try:
            if secret:
                value = inquirer.secret(  # type: ignore[union-attr]
                    message=message,
                ).execute()
            else:
                value = inquirer.text(  # type: ignore[union-attr]
                    message=message,
                    default=default,
                ).execute()
        except KeyboardInterrupt as exc:
            raise click.Abort() from exc
        return str(value)

    return str(
        click.prompt(
            message,
            default=default,
            show_default=show_default,
            hide_input=secret,
        )
    )


def _should_use_inquirer(cli_context: CLIContext) -> bool:
    if inquirer is None:
        return False
    if cli_context.non_interactive:
        return False
    return bool(sys.stdin.isatty() and sys.stdout.isatty())
