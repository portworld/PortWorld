from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import click

from backend.cli_app.context import CLIContext


@dataclass(frozen=True, slots=True)
class DiagnosticCheck:
    id: str
    status: str
    message: str
    action: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "status": self.status,
            "message": self.message,
        }
        if self.action is not None:
            payload["action"] = self.action
        return payload


@dataclass(frozen=True, slots=True)
class CommandResult:
    ok: bool
    command: str
    message: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    checks: tuple[DiagnosticCheck, ...] = ()
    exit_code: int = 0

    def __post_init__(self) -> None:
        if self.exit_code == 0 and not self.ok:
            object.__setattr__(self, "exit_code", 1)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "command": self.command,
        }
        if self.message is not None:
            payload["message"] = self.message
        if self.data:
            payload.update(self.data)
        if self.checks:
            payload["checks"] = [check.to_dict() for check in self.checks]
        payload["exit_code"] = self.exit_code
        return payload


def emit_result(cli_context: CLIContext, result: CommandResult) -> None:
    if cli_context.json_output:
        click.echo(json.dumps(result.to_dict(), ensure_ascii=True, indent=2, sort_keys=True))
        return

    status_label = "OK" if result.ok else "FAIL"
    click.echo(f"{status_label}: {result.command}")
    if result.message:
        click.echo(result.message)
    for check in result.checks:
        click.echo(f"{check.status.upper()}: {check.id} - {check.message}")
        if check.action:
            click.echo(f"  next: {check.action}")


def exit_with_result(cli_context: CLIContext, result: CommandResult) -> None:
    emit_result(cli_context, result)
    raise click.exceptions.Exit(result.exit_code)


def format_key_value_lines(*pairs: tuple[str, object | None]) -> str:
    lines: list[str] = []
    for key, value in pairs:
        if value is None:
            continue
        if isinstance(value, bool):
            rendered = "yes" if value else "no"
        else:
            rendered = str(value)
        lines.append(f"{key}: {rendered}")
    return "\n".join(lines)
