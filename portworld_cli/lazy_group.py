from __future__ import annotations

from importlib import import_module

import click


LazyCommandSpec = tuple[str, str, str]


class LazyGroup(click.Group):
    """Load top-level command modules only when the command is requested."""

    def __init__(
        self,
        *args,
        lazy_commands: dict[str, LazyCommandSpec] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._lazy_commands = dict(lazy_commands or {})

    def list_commands(self, ctx: click.Context) -> list[str]:
        return sorted({*self.commands.keys(), *self._lazy_commands.keys()})

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        command = self.commands.get(cmd_name)
        if command is not None:
            return command

        spec = self._lazy_commands.get(cmd_name)
        if spec is None:
            return None

        module_name, attr_name, _short_help = spec
        module = import_module(module_name)
        command = getattr(module, attr_name)
        self.add_command(command, cmd_name)
        return command

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        rows: list[tuple[str, str]] = []
        for subcommand in self.list_commands(ctx):
            command = self.commands.get(subcommand)
            if command is None:
                _module_name, _attr_name, short_help = self._lazy_commands[subcommand]
                rows.append((subcommand, short_help))
                continue
            rows.append((subcommand, command.get_short_help_str()))

        if rows:
            with formatter.section("Commands"):
                formatter.write_dl(rows)
