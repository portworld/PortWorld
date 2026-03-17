from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from portworld_cli.envfile import EnvTemplate, ParsedEnvFile, load_env_template, parse_env_file
from portworld_cli.workspace.discovery.paths import ProjectPaths, WorkspacePaths
from portworld_cli.workspace.project_config import (
    ProjectConfig,
    GCP_CLOUD_RUN_TARGET,
    RUNTIME_SOURCE_PUBLISHED,
    RUNTIME_SOURCE_SOURCE,
    derive_project_config,
    load_project_config_record,
)
from portworld_cli.targets import MANAGED_TARGETS
from portworld_cli.workspace.published import load_published_env_template
from portworld_cli.workspace.state.state_store import read_json_state


@dataclass(frozen=True, slots=True)
class WorkspaceStoreSnapshot:
    project_paths: ProjectPaths | None
    template: EnvTemplate | None
    existing_env: ParsedEnvFile | None
    project_config: ProjectConfig
    derived_from_legacy: bool
    configured_runtime_source: str | None
    effective_runtime_source: str
    runtime_source_derived_from_legacy: bool
    remembered_deploy_state: dict[str, Any]


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
    remembered_deploy_state = read_json_state(
        workspace_paths.state_file_for_target(GCP_CLOUD_RUN_TARGET)
    )
    loaded_project_config = load_project_config_record(workspace_paths.project_config_file)
    project_config = None if loaded_project_config is None else loaded_project_config.config
    derived_from_legacy = project_config is None
    if project_config is None:
        env_values = {} if template is None or existing_env is None else template.defaults()
        if existing_env is not None:
            env_values.update(existing_env.known_values)
        project_config = derive_project_config(
            env_values=env_values,
            deploy_state=remembered_deploy_state,
            default_runtime_source=(
                RUNTIME_SOURCE_SOURCE if project_paths is not None else RUNTIME_SOURCE_PUBLISHED
            ),
        )
        configured_runtime_source = None
        runtime_source_derived_from_legacy = True
    else:
        configured_runtime_source = project_config.runtime_source
        runtime_source_derived_from_legacy = not loaded_project_config.runtime_source_explicit

    preferred_target = (
        None if project_config is None else project_config.deploy.preferred_target
    )
    state_target = (
        preferred_target if preferred_target in MANAGED_TARGETS else GCP_CLOUD_RUN_TARGET
    )
    if state_target != GCP_CLOUD_RUN_TARGET:
        remembered_deploy_state = read_json_state(
            workspace_paths.state_file_for_target(state_target)
        )
    if not remembered_deploy_state:
        remembered_deploy_state = read_json_state(
            workspace_paths.state_file_for_target(GCP_CLOUD_RUN_TARGET)
        )

    effective_runtime_source = configured_runtime_source or (
        RUNTIME_SOURCE_SOURCE if project_paths is not None else RUNTIME_SOURCE_PUBLISHED
    )
    return WorkspaceStoreSnapshot(
        project_paths=project_paths,
        template=template,
        existing_env=existing_env,
        project_config=project_config,
        derived_from_legacy=derived_from_legacy,
        configured_runtime_source=configured_runtime_source,
        effective_runtime_source=effective_runtime_source,
        runtime_source_derived_from_legacy=runtime_source_derived_from_legacy,
        remembered_deploy_state=remembered_deploy_state,
    )
