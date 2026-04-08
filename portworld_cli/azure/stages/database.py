from __future__ import annotations

from portworld_cli.azure.client import AzureAdapters
from portworld_cli.azure.stages.config import ResolvedAzureDeployConfig
from portworld_cli.deploy.config import DeployStageError


def ensure_postgres_and_database_url(
    config: ResolvedAzureDeployConfig,
    *,
    stage_records: list[dict[str, object]],
    adapters: AzureAdapters,
) -> str:
    del config, stage_records, adapters
    raise DeployStageError(
        stage="postgres_provision",
        message="Azure one-click deploy no longer provisions PostgreSQL in memory/context v2.",
        action="Remove database-url assumptions and rely on managed object-store runtime configuration.",
    )
