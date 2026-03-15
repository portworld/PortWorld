from __future__ import annotations

from collections import OrderedDict

from backend.cli_app.context import CLIContext
from backend.cli_app.output import CommandResult, format_key_value_lines
from backend.cli_app.provider_catalog import list_providers, resolve_provider, supported_provider_ids


LIST_COMMAND_NAME = "portworld providers list"
SHOW_COMMAND_NAME = "portworld providers show"


class ProviderUsageError(RuntimeError):
    pass


def run_providers_list(cli_context: CLIContext) -> CommandResult:
    providers = list_providers()
    grouped: OrderedDict[str, list] = OrderedDict()
    for provider in providers:
        grouped.setdefault(provider.kind, []).append(provider)

    sections: list[str] = []
    for kind, entries in grouped.items():
        lines = [kind]
        for entry in entries:
            default_label = "yes" if entry.default else "no"
            lines.append(
                f"- {entry.id}: {entry.summary} (default: {default_label})"
            )
        sections.append("\n".join(lines))

    return CommandResult(
        ok=True,
        command=LIST_COMMAND_NAME,
        message="\n\n".join(sections),
        data={"providers": [provider.to_summary_payload() for provider in providers]},
        exit_code=0,
    )


def run_providers_show(cli_context: CLIContext, provider_id: str) -> CommandResult:
    provider = resolve_provider(provider_id)
    if provider is None:
        supported = ", ".join(supported_provider_ids())
        return CommandResult(
            ok=False,
            command=SHOW_COMMAND_NAME,
            message=f"Unsupported provider '{provider_id}'. Supported values: {supported}.",
            data={
                "status": "error",
                "error_type": ProviderUsageError.__name__,
                "supported_provider_ids": list(supported_provider_ids()),
            },
            exit_code=2,
        )

    sections = [
        "\n".join(
            [
                provider.display_name,
                format_key_value_lines(
                    ("id", provider.id),
                    ("kind", provider.kind),
                    ("default", provider.default),
                    ("summary", provider.summary),
                ),
            ]
        )
    ]
    if provider.aliases:
        sections.append("aliases: " + ", ".join(provider.aliases))
    if provider.capability_tags:
        sections.append("capabilities: " + ", ".join(provider.capability_tags))
    if provider.supported_targets:
        sections.append("supported_targets: " + ", ".join(provider.supported_targets))
    if provider.required_clis:
        sections.append("required_clis: " + ", ".join(provider.required_clis))
    if provider.required_env_keys:
        sections.append("required_env_keys: " + ", ".join(provider.required_env_keys))
    if provider.optional_env_keys:
        sections.append("optional_env_keys: " + ", ".join(provider.optional_env_keys))
    if provider.command_paths:
        sections.append("command_paths:\n" + "\n".join(f"- {path}" for path in provider.command_paths))
    if provider.setup_notes:
        sections.append("setup_notes:\n" + "\n".join(f"- {note}" for note in provider.setup_notes))

    return CommandResult(
        ok=True,
        command=SHOW_COMMAND_NAME,
        message="\n\n".join(sections),
        data={"provider_id": provider.id, "provider": provider.to_detail_payload()},
        exit_code=0,
    )
