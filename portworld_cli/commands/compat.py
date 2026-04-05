from __future__ import annotations

import click


# Temporary public compatibility surface for removed pre-provider-scoped secret flags.
# Keep these migration errors stable through the first PyPI release, then revisit.
def reject_legacy_secret_flag(
    _ctx: click.Context,
    param: click.Parameter,
    value: str | None,
) -> None:
    if value is None:
        return None
    migration_targets = {
        "openai_api_key": "--realtime-api-key",
        "vision_provider_api_key": "--vision-api-key",
        "tavily_api_key": "--search-api-key",
    }
    replacement = migration_targets.get(param.name, "the canonical provider-scoped flag")
    raise click.UsageError(
        f"{param.opts[0]} has been removed. Use {replacement} instead."
    )
