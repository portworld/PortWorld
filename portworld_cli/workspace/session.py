from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from backend.core.provider_requirements import (
    build_provider_requirement_diagnostics,
    compute_selected_provider_key_set,
    resolve_selected_providers,
)
from portworld_cli.context import CLIContext
from portworld_cli.deploy_state import DeployState
from portworld_cli.envfile import EnvTemplate, ParsedEnvFile
from portworld_cli.workspace.discovery.paths import (
    ProjectPaths,
    ProjectRootResolutionError,
    WorkspacePaths,
)
from portworld_cli.workspace.project_config import (
    GCP_CLOUD_RUN_TARGET,
    RUNTIME_SOURCE_PUBLISHED,
    RUNTIME_SOURCE_SOURCE,
    ProjectConfig,
    build_env_overrides_from_project_config,
)
from portworld_cli.targets import MANAGED_TARGETS
from portworld_cli.workspace.discovery.locator import ResolvedWorkspace, resolve_workspace
from portworld_cli.workspace.store import WorkspaceStoreSnapshot, load_workspace_store
from portworld_cli.workspace.state.state_store import read_json_state
from portworld_cli.workspace.state.state_store import CLIStateError


@dataclass(frozen=True, slots=True)
class SecretReadiness:
    selected_realtime_provider: str
    selected_vision_provider: str | None
    selected_search_provider: str | None
    required_secret_keys: tuple[str, ...]
    optional_secret_keys: tuple[str, ...]
    missing_required_secret_keys: tuple[str, ...]
    required_config_keys: tuple[str, ...]
    optional_config_keys: tuple[str, ...]
    missing_required_config_keys: tuple[str, ...]
    key_presence: dict[str, bool | None]
    config_key_presence: dict[str, bool | None]
    bearer_token_present: bool | None

    def to_dict(self) -> dict[str, object]:
        return {
            "selected_realtime_provider": self.selected_realtime_provider,
            "selected_vision_provider": self.selected_vision_provider,
            "selected_search_provider": self.selected_search_provider,
            "required_secret_keys": list(self.required_secret_keys),
            "optional_secret_keys": list(self.optional_secret_keys),
            "missing_required_secret_keys": list(self.missing_required_secret_keys),
            "required_config_keys": list(self.required_config_keys),
            "optional_config_keys": list(self.optional_config_keys),
            "missing_required_config_keys": list(self.missing_required_config_keys),
            "key_presence": dict(self.key_presence),
            "config_key_presence": dict(self.config_key_presence),
            "openai_api_key_present": self.openai_api_key_present,
            "vision_provider_secret_required": self.vision_provider_secret_required,
            "vision_provider_api_key_present": self.vision_provider_api_key_present,
            "tavily_secret_required": self.tavily_secret_required,
            "tavily_api_key_present": self.tavily_api_key_present,
            "bearer_token_present": self.bearer_token_present,
        }

    @property
    def openai_api_key_present(self) -> bool | None:
        return self.key_presence.get("OPENAI_API_KEY")

    @property
    def vision_provider_secret_required(self) -> bool:
        return any(
            key.startswith("VISION_") and key.endswith("_API_KEY")
            for key in self.required_secret_keys
        )

    @property
    def vision_provider_api_key_present(self) -> bool | None:
        if not self.vision_provider_secret_required:
            return None
        for key in self.required_secret_keys:
            if key.startswith("VISION_") and key.endswith("_API_KEY"):
                return self.key_presence.get(key)
        return False

    @property
    def tavily_secret_required(self) -> bool:
        return "TAVILY_API_KEY" in self.required_secret_keys

    @property
    def tavily_api_key_present(self) -> bool | None:
        if not self.tavily_secret_required:
            return None
        return self.key_presence.get("TAVILY_API_KEY")


@dataclass(frozen=True, slots=True)
class WorkspaceSession:
    cli_context: CLIContext
    workspace_paths: WorkspacePaths
    project_paths: ProjectPaths | None
    template: EnvTemplate | None
    existing_env: ParsedEnvFile | None
    project_config: ProjectConfig
    configured_runtime_source: str
    effective_runtime_source: str
    remembered_deploy_state: dict[str, Any]
    remembered_deploy_state_target: str | None
    workspace_resolution_source: str
    active_workspace_root: Path | None

    def merged_env_values(self) -> dict[str, str]:
        if self.template is None or self.existing_env is None:
            return {}
        env_values = self.template.defaults()
        env_values.update(
            _build_effective_env_values(
                template=self.template,
                existing_env=self.existing_env,
                config_overrides=build_env_overrides_from_project_config(self.project_config),
            )
        )
        return dict(env_values)

    def secret_readiness(self) -> SecretReadiness:
        config_selection = build_env_overrides_from_project_config(self.project_config)
        selected = resolve_selected_providers(config_selection)
        key_set = compute_selected_provider_key_set(selected)

        if self.existing_env is None:
            key_presence = {key: None for key in key_set.required_secret_env_keys}
            config_key_presence = {key: None for key in key_set.required_non_secret_env_keys}
            return SecretReadiness(
                selected_realtime_provider=selected.realtime_provider,
                selected_vision_provider=selected.vision_provider,
                selected_search_provider=selected.search_provider,
                required_secret_keys=key_set.required_secret_env_keys,
                optional_secret_keys=key_set.optional_secret_env_keys,
                missing_required_secret_keys=(),
                required_config_keys=key_set.required_non_secret_env_keys,
                optional_config_keys=key_set.optional_non_secret_env_keys,
                missing_required_config_keys=(),
                key_presence=key_presence,
                config_key_presence=config_key_presence,
                bearer_token_present=None,
            )

        env_values = _build_effective_env_values(
            template=self.template,
            existing_env=self.existing_env,
            config_overrides=config_selection,
        )
        diagnostics = build_provider_requirement_diagnostics(
            env_values,
            selected=selected,
        )
        key_presence = {
            key: diagnostics.secret_key_presence.get(key, False)
            for key in diagnostics.required_secret_env_keys
        }
        config_key_presence = {
            key: diagnostics.non_secret_key_presence.get(key, False)
            for key in diagnostics.required_non_secret_env_keys
        }

        return SecretReadiness(
            selected_realtime_provider=diagnostics.selected.realtime_provider,
            selected_vision_provider=diagnostics.selected.vision_provider,
            selected_search_provider=diagnostics.selected.search_provider,
            required_secret_keys=diagnostics.required_secret_env_keys,
            optional_secret_keys=diagnostics.optional_secret_env_keys,
            missing_required_secret_keys=diagnostics.missing_required_secret_env_keys,
            required_config_keys=diagnostics.required_non_secret_env_keys,
            optional_config_keys=diagnostics.optional_non_secret_env_keys,
            missing_required_config_keys=diagnostics.missing_required_non_secret_env_keys,
            key_presence=key_presence,
            config_key_presence=config_key_presence,
            bearer_token_present=bool((env_values.get("BACKEND_BEARER_TOKEN", "")).strip()),
        )

    @property
    def workspace_root(self) -> Path:
        return self.workspace_paths.workspace_root

    @property
    def env_path(self) -> Path | None:
        if self.project_paths is not None:
            return self.project_paths.env_file
        if self.effective_runtime_source == RUNTIME_SOURCE_PUBLISHED:
            return self.workspace_paths.workspace_env_file
        return None


@dataclass(frozen=True, slots=True)
class SourceWorkspaceSession(WorkspaceSession):
    pass


@dataclass(frozen=True, slots=True)
class PublishedWorkspaceSession(WorkspaceSession):
    pass


@dataclass(frozen=True, slots=True)
class InspectionSession:
    config_session: WorkspaceSession
    deploy_state: DeployState
    deploy_states_by_target: dict[str, DeployState]
    deploy_state_errors_by_target: dict[str, str]

    @property
    def project_config(self) -> ProjectConfig:
        return self.config_session.project_config

    def active_target(self) -> str | None:
        if self.deploy_state.has_data():
            if self.config_session.remembered_deploy_state_target in MANAGED_TARGETS:
                return self.config_session.remembered_deploy_state_target
            preferred_target = self.project_config.deploy.preferred_target
            if preferred_target in MANAGED_TARGETS:
                return preferred_target
            return GCP_CLOUD_RUN_TARGET
        preferred_target = self.project_config.deploy.preferred_target
        if preferred_target in MANAGED_TARGETS:
            return preferred_target
        return None


@dataclass(frozen=True, slots=True)
class ResolvedGCPInspectionTarget:
    project_id: str | None
    region: str | None
    service_name: str | None

    def is_complete(self) -> bool:
        return bool(self.project_id and self.region and self.service_name)


def load_workspace_session(cli_context: CLIContext) -> WorkspaceSession:
    resolved_workspace = resolve_workspace(
        explicit_root=cli_context.project_root_override,
    )
    return _load_workspace_session(
        cli_context,
        resolved_workspace=resolved_workspace,
    )


def build_workspace_session(
    cli_context: CLIContext,
    *,
    workspace_paths: WorkspacePaths,
    workspace_resolution_source: str,
    active_workspace_root: Path | None,
) -> WorkspaceSession:
    return _build_workspace_session(
        cli_context=cli_context,
        workspace_paths=workspace_paths,
        store_snapshot=load_workspace_store(workspace_paths),
        workspace_resolution_source=workspace_resolution_source,
        active_workspace_root=active_workspace_root,
    )


def require_source_workspace_session(
    session: WorkspaceSession,
    *,
    command_name: str,
    requested_runtime_source: str | None = None,
    usage_error_type: type[Exception] = RuntimeError,
) -> SourceWorkspaceSession:
    if requested_runtime_source == RUNTIME_SOURCE_PUBLISHED:
        raise usage_error_type(
            f"{command_name} is not supported when runtime_source=published yet. "
            "Use `portworld config edit cloud --runtime-source published` to record published mode, "
            "and switch back with `portworld config edit cloud --runtime-source source` when you need source-backed commands."
        )

    effective_runtime_source = requested_runtime_source or session.effective_runtime_source
    if effective_runtime_source == RUNTIME_SOURCE_PUBLISHED:
        raise usage_error_type(
            f"{command_name} is not supported when runtime_source=published yet. "
            "Switch back with `portworld config edit cloud --runtime-source source`."
        )

    if session.project_paths is None or session.template is None or session.existing_env is None:
        raise ProjectRootResolutionError(
            f"{command_name} requires a PortWorld source checkout with backend/Dockerfile, "
            "backend/.env.example, and docker-compose.yml."
        )

    if session.project_config.runtime_source == RUNTIME_SOURCE_SOURCE:
        if isinstance(session, SourceWorkspaceSession):
            return session
        return SourceWorkspaceSession(**_session_kwargs(session))

    return SourceWorkspaceSession(
        **_session_kwargs(
            replace(
                session,
                project_config=replace(session.project_config, runtime_source=RUNTIME_SOURCE_SOURCE),
                configured_runtime_source=RUNTIME_SOURCE_SOURCE,
                effective_runtime_source=RUNTIME_SOURCE_SOURCE,
            )
        )
    )


def load_inspection_session(cli_context: CLIContext) -> InspectionSession:
    config_session = load_workspace_session(cli_context)
    deploy_states_by_target, deploy_state_errors_by_target = _load_deploy_states_by_target(config_session.workspace_paths)
    return InspectionSession(
        config_session=config_session,
        deploy_state=DeployState.from_payload(config_session.remembered_deploy_state),
        deploy_states_by_target=deploy_states_by_target,
        deploy_state_errors_by_target=deploy_state_errors_by_target,
    )


def resolve_gcp_inspection_target(
    session: InspectionSession,
    *,
    project_id: str | None = None,
    region: str | None = None,
    service_name: str | None = None,
) -> ResolvedGCPInspectionTarget:
    gcp_config = session.project_config.deploy.gcp_cloud_run
    return ResolvedGCPInspectionTarget(
        project_id=_strip(project_id) or session.deploy_state.project_id or _strip(gcp_config.project_id),
        region=_strip(region) or session.deploy_state.region or _strip(gcp_config.region),
        service_name=_strip(service_name)
        or session.deploy_state.service_name
        or _strip(gcp_config.service_name),
    )


def _load_deploy_states_by_target(
    workspace_paths: WorkspacePaths,
) -> tuple[dict[str, DeployState], dict[str, str]]:
    deploy_states: dict[str, DeployState] = {}
    errors: dict[str, str] = {}
    for target in MANAGED_TARGETS:
        try:
            payload = read_json_state(workspace_paths.state_file_for_target(target))
            deploy_states[target] = DeployState.from_payload(payload)
        except CLIStateError as exc:
            deploy_states[target] = DeployState.from_payload({})
            errors[target] = str(exc)
    return deploy_states, errors


def _load_workspace_session(
    cli_context: CLIContext,
    *,
    resolved_workspace: ResolvedWorkspace,
) -> WorkspaceSession:
    return _build_workspace_session(
        cli_context=cli_context,
        workspace_paths=resolved_workspace.workspace_paths,
        store_snapshot=load_workspace_store(resolved_workspace.workspace_paths),
        workspace_resolution_source=resolved_workspace.workspace_resolution_source,
        active_workspace_root=resolved_workspace.active_workspace_root,
    )


def _build_workspace_session(
    *,
    cli_context: CLIContext,
    workspace_paths: WorkspacePaths,
    store_snapshot: WorkspaceStoreSnapshot,
    workspace_resolution_source: str,
    active_workspace_root: Path | None,
) -> WorkspaceSession:
    session_cls: type[WorkspaceSession] = WorkspaceSession
    if store_snapshot.effective_runtime_source == RUNTIME_SOURCE_PUBLISHED:
        session_cls = PublishedWorkspaceSession
    elif (
        store_snapshot.project_paths is not None
        and store_snapshot.template is not None
        and store_snapshot.existing_env is not None
    ):
        session_cls = SourceWorkspaceSession

    return session_cls(
        cli_context=cli_context,
        workspace_paths=workspace_paths,
        project_paths=store_snapshot.project_paths,
        template=store_snapshot.template,
        existing_env=store_snapshot.existing_env,
        project_config=store_snapshot.project_config,
        configured_runtime_source=store_snapshot.configured_runtime_source,
        effective_runtime_source=store_snapshot.effective_runtime_source,
        remembered_deploy_state=store_snapshot.remembered_deploy_state,
        remembered_deploy_state_target=store_snapshot.remembered_deploy_state_target,
        workspace_resolution_source=workspace_resolution_source,
        active_workspace_root=active_workspace_root,
    )


def _session_kwargs(session: WorkspaceSession) -> dict[str, Any]:
    return {
        "cli_context": session.cli_context,
        "workspace_paths": session.workspace_paths,
        "project_paths": session.project_paths,
        "template": session.template,
        "existing_env": session.existing_env,
        "project_config": session.project_config,
        "configured_runtime_source": session.configured_runtime_source,
        "effective_runtime_source": session.effective_runtime_source,
        "remembered_deploy_state": session.remembered_deploy_state,
        "remembered_deploy_state_target": session.remembered_deploy_state_target,
        "workspace_resolution_source": session.workspace_resolution_source,
        "active_workspace_root": session.active_workspace_root,
    }


def _build_effective_env_values(
    *,
    template: EnvTemplate,
    existing_env: ParsedEnvFile,
    config_overrides: dict[str, str],
) -> dict[str, str]:
    values = dict(template.defaults())
    values.update(existing_env.known_values)
    values.update(existing_env.preserved_overrides)
    values.update(config_overrides)
    return {key: str(value) for key, value in values.items()}


def _strip(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None
