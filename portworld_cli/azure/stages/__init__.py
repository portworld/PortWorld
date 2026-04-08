"""Azure deploy stage helpers."""

from portworld_cli.azure.stages.artifacts import (
    ensure_acr,
    ensure_resource_group,
    ensure_resource_provider,
    ensure_storage,
)
from portworld_cli.azure.stages.config import (
    DeployAzureContainerAppsOptions,
    ResolvedAzureDeployConfig,
    resolve_azure_deploy_config,
)
from portworld_cli.azure.stages.container_app_runtime import (
    AzureDeployMutationResult,
    build_runtime_env_vars,
    resolve_container_app_fqdn,
    run_azure_deploy_mutations,
    sanitize_runtime_env_for_output,
    split_runtime_env_for_azure,
)
from portworld_cli.azure.stages.shared import now_ms, stage_ok
from portworld_cli.azure.stages.validation import probe_livez, probe_ws

__all__ = (
    "AzureDeployMutationResult",
    "DeployAzureContainerAppsOptions",
    "ResolvedAzureDeployConfig",
    "build_runtime_env_vars",
    "ensure_acr",
    "ensure_resource_group",
    "ensure_resource_provider",
    "ensure_storage",
    "now_ms",
    "probe_livez",
    "probe_ws",
    "resolve_azure_deploy_config",
    "resolve_container_app_fqdn",
    "run_azure_deploy_mutations",
    "sanitize_runtime_env_for_output",
    "split_runtime_env_for_azure",
    "stage_ok",
)
