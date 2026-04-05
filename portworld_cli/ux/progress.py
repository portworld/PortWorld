from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from portworld_cli.context import CLIContext

try:
    from rich.console import Console, Group
    from rich.live import Live
    from rich.spinner import Spinner
    from rich.text import Text
except Exception:  # pragma: no cover - optional dependency fallback
    Console = None
    Group = None
    Live = None
    Spinner = None
    Text = None


class ProgressReporter:
    def __init__(
        self,
        cli_context: CLIContext,
        *,
        enabled: bool | None = None,
    ) -> None:
        self._cli_context = cli_context
        self._enabled = self._resolve_enabled(enabled)
        self._console = Console(stderr=True) if self._enabled and Console is not None else None
        self._live = None
        self._history: list[tuple[str, str]] = []
        self._active_label: str | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def snapshot(self) -> dict[str, object]:
        return {
            "enabled": self._enabled,
            "history": list(self._history),
            "active_label": self._active_label,
        }

    @contextmanager
    def stage(self, label: str) -> Iterator[None]:
        self.start(label)
        try:
            yield
        except Exception:
            self.fail()
            raise
        else:
            self.complete()

    def start(self, label: str) -> None:
        if not self._enabled:
            return
        if self._active_label is not None:
            self.complete()
        self._active_label = label
        self._ensure_live()
        self._refresh()

    def complete(self) -> None:
        if not self._enabled or self._active_label is None:
            return
        self._history.append(("done", self._active_label))
        self._active_label = None
        self._refresh()

    def fail(self) -> None:
        if not self._enabled or self._active_label is None:
            return
        self._history.append(("failed", self._active_label))
        self._active_label = None
        self._refresh()

    def close(self) -> None:
        if self._live is None:
            return
        self._refresh()
        self._live.stop()
        self._live = None

    def _resolve_enabled(self, enabled: bool | None) -> bool:
        if enabled is not None:
            return enabled
        if self._cli_context.json_output or self._cli_context.non_interactive:
            return False
        if Console is None or Group is None or Live is None or Spinner is None or Text is None:
            return False
        probe_console = Console(stderr=True)
        return bool(probe_console.is_terminal and probe_console.is_interactive)

    def _ensure_live(self) -> None:
        if not self._enabled or self._live is not None or self._console is None:
            return
        self._live = Live(
            self._renderable(),
            console=self._console,
            refresh_per_second=12,
            transient=False,
        )
        self._live.start()

    def _refresh(self) -> None:
        if self._live is None:
            return
        self._live.update(self._renderable(), refresh=True)

    def _renderable(self):
        if Group is None or Spinner is None or Text is None:
            return ""
        lines: list[object] = []
        for status, label in self._history:
            if status == "done":
                lines.append(Text(f"✓ {label}", style="green"))
            elif status == "failed":
                lines.append(Text(f"✗ {label}", style="red"))
        if self._active_label is not None:
            lines.append(Spinner("dots", text=self._active_label, style="cyan"))
        if not lines:
            lines.append(Text(""))
        return Group(*lines)
