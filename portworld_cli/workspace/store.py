from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from portworld_cli.envfile import EnvTemplate, ParsedEnvFile, load_env_template, parse_env_file
from portworld_cli.workspace.discovery.paths import ProjectPaths, WorkspacePaths
from portworld_cli.workspace.discovery.paths import ProjectRootResolutionError
from portworld_cli.workspace.project_config import (
    ProjectConfig,
    GCP_CLOUD_RUN_TARGET,
    load_project_config_record,
)
from portworld_cli.targets import MANAGED_TARGETS
from portworld_cli.workspace.published import load_published_env_template
from portworld_cli.workspace.state.state_store import CLIStateError, read_json_state


@dataclass(frozen=True, slots=True)
class WorkspaceStoreSnapshot:
    project_paths: ProjectPaths | None
    template: EnvTemplate | None
    existing_env: ParsedEnvFile | None
    project_config: ProjectConfig
    configured_runtime_source: str
    effective_runtime_source: str
    remembered_deploy_state: dict[str, Any]
    remembered_deploy_state_target: str | None


def load_workspace_store(workspace_paths: WorkspacePaths) -> WorkspaceStoreSnapshot:
    project_paths = workspace_paths.source_project_paths
    template = (
        load_env_template(project_paths.env_example_file)
        if project_paths is not None
        else load_published_env_template()
    )
    env_path = (
        project_paths.env_file
        if project_paths is not None
        else workspace_paths.workspace_env_file
    )
    existing_env = None if template is None else parse_env_file(env_path, template=template)
    remembered_deploy_state = _safe_read_json_state(
        workspace_paths.state_file_for_target(GCP_CLOUD_RUN_TARGET)
    )
    remembered_deploy_state_target: str | None = (
        GCP_CLOUD_RUN_TARGET if remembered_deploy_state else None
    )
    loaded_project_config = load_project_config_record(workspace_paths.project_config_file)
    if loaded_project_config is None:
        raise ProjectRootResolutionError(
            f"{workspace_paths.project_config_file} is missing. Run `portworld init` first."
        )
    project_config = loaded_project_config.config
    configured_runtime_source = project_config.runtime_source

    preferred_target = project_config.deploy.preferred_target
    state_target = (
        preferred_target if preferred_target in MANAGED_TARGETS else GCP_CLOUD_RUN_TARGET
    )
    if state_target != GCP_CLOUD_RUN_TARGET:
        preferred_state = _safe_read_json_state(
            workspace_paths.state_file_for_target(state_target)
        )
        if preferred_state:
            remembered_deploy_state = preferred_state
            remembered_deploy_state_target = state_target
    if not remembered_deploy_state:
        fallback_state = _safe_read_json_state(
            workspace_paths.state_file_for_target(GCP_CLOUD_RUN_TARGET)
        )
        if fallback_state:
            remembered_deploy_state = fallback_state
            remembered_deploy_state_target = GCP_CLOUD_RUN_TARGET

    effective_runtime_source = configured_runtime_source
    return WorkspaceStoreSnapshot(
        project_paths=project_paths,
        template=template,
        existing_env=existing_env,
        project_config=project_config,
        configured_runtime_source=configured_runtime_source,
        effective_runtime_source=effective_runtime_source,
        remembered_deploy_state=remembered_deploy_state,
        remembered_deploy_state_target=remembered_deploy_state_target,
    )


def _safe_read_json_state(path) -> dict[str, Any]:
    try:
        return read_json_state(path)
    except CLIStateError:
        return {}
