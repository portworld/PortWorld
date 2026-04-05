from __future__ import annotations

import re
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from portworld_cli.output import CommandResult

try:
    from rich import box
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text
except Exception:  # pragma: no cover - optional dependency fallback
    Console = None


_KV_LINE_RE = re.compile(r"^([^:]+):(.*)$")


def emit_command_result(result: "CommandResult") -> bool:
    if not _should_use_rich():
        return False
    console = Console()
    _render_header(console, result)
    if result.message:
        _render_message(console, result.message)
    if result.checks:
        console.print()
        checks_table = Table(
            title="Checks",
            box=box.SIMPLE_HEAVY,
            show_header=True,
            header_style="bold",
        )
        checks_table.add_column("Status", style="bold")
        checks_table.add_column("ID", style="cyan")
        checks_table.add_column("Message")
        checks_table.add_column("Next Step", style="yellow")
        for check in result.checks:
            checks_table.add_row(
                check.status.upper(),
                check.id,
                check.message,
                check.action or "",
            )
        console.print(checks_table)
    return True


def _should_use_rich() -> bool:
    if Console is None:
        return False
    return bool(sys.stdout.isatty())


def _render_header(console: Console, result: CommandResult) -> None:
    label = "OK" if result.ok else "FAIL"
    style = "green" if result.ok else "red"
    text = Text()
    text.append(label, style=f"bold {style}")
    text.append(" ")
    text.append(result.command, style="bold")
    console.print(text)


def _render_message(console: Console, message: str) -> None:
    sections = [section.strip() for section in message.split("\n\n") if section.strip()]
    for idx, section in enumerate(sections):
        if idx > 0:
            console.print()
        _render_section(console, section)


def _render_section(console: Console, section: str) -> None:
    lines = [line.rstrip() for line in section.splitlines() if line.strip()]
    if not lines:
        return

    title: str | None = None
    data_lines = lines
    if len(lines) > 1 and _is_header_line(lines[0]) and not _parse_key_value(lines[0]):
        title = lines[0]
        data_lines = lines[1:]

    kv_pairs: list[tuple[str, str]] = []
    text_lines: list[str] = []
    for line in data_lines:
        parsed = _parse_key_value(line)
        if parsed is None:
            text_lines.append(line)
            continue
        key, value = parsed
        kv_pairs.append((key, value))

    if title:
        console.print(f"[bold]{title}[/bold]")
    if kv_pairs:
        table = Table(
            box=box.SIMPLE,
            show_header=False,
            pad_edge=False,
        )
        table.add_column("k", style="cyan")
        table.add_column("v")
        for key, value in kv_pairs:
            table.add_row(key, value)
        console.print(table)
    if text_lines:
        for line in text_lines:
            if line.startswith("- "):
                console.print(f"• {line[2:]}")
            else:
                console.print(line)


def _parse_key_value(line: str) -> tuple[str, str] | None:
    match = _KV_LINE_RE.match(line)
    if match is None:
        return None
    key = match.group(1).strip()
    value = match.group(2).strip()
    if not key:
        return None
    return key, value


def _is_header_line(line: str) -> bool:
    lowered = line.strip().lower()
    return bool(lowered) and (":" not in lowered) and not lowered.startswith("-")
