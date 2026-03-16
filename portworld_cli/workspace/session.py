from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from portworld_cli.context import CLIContext
from portworld_cli.deploy_state import DeployState
from portworld_cli.envfile import EnvTemplate, ParsedEnvFile
from portworld_cli.workspace.paths import ProjectPaths, ProjectRootResolutionError, WorkspacePaths
from portworld_cli.workspace.project_config import (
    GCP_CLOUD_RUN_TARGET,
    RUNTIME_SOURCE_PUBLISHED,
    RUNTIME_SOURCE_SOURCE,
    ProjectConfig,
)
from portworld_cli.workspace.locator import ResolvedWorkspace, resolve_workspace
from portworld_cli.workspace.store import WorkspaceStoreSnapshot, load_workspace_store


@dataclass(frozen=True, slots=True)
class SecretReadiness:
    openai_api_key_present: bool | None
    vision_provider_secret_required: bool
    vision_provider_api_key_present: bool | None
    tavily_secret_required: bool
    tavily_api_key_present: bool | None
    bearer_token_present: bool | None

    def to_dict(self) -> dict[str, object]:
        return {
            "openai_api_key_present": self.openai_api_key_present,
            "vision_provider_secret_required": self.vision_provider_secret_required,
            "vision_provider_api_key_present": self.vision_provider_api_key_present,
            "tavily_secret_required": self.tavily_secret_required,
            "tavily_api_key_present": self.tavily_api_key_present,
            "bearer_token_present": self.bearer_token_present,
        }


@dataclass(frozen=True, slots=True)
class WorkspaceSession:
    cli_context: CLIContext
    workspace_paths: WorkspacePaths
    project_paths: ProjectPaths | None
    template: EnvTemplate | None
    existing_env: ParsedEnvFile | None
    project_config: ProjectConfig
    derived_from_legacy: bool
    configured_runtime_source: str | None
    effective_runtime_source: str
    runtime_source_derived_from_legacy: bool
    remembered_deploy_state: dict[str, Any]
    workspace_resolution_source: str
    active_workspace_root: Path | None

    def merged_env_values(self) -> dict[str, str]:
        if self.template is None or self.existing_env is None:
            return {}
        env_values = self.template.defaults()
        env_values.update(self.existing_env.known_values)
        return dict(env_values)

    def secret_readiness(self) -> SecretReadiness:
        if self.existing_env is None:
            return SecretReadiness(
                openai_api_key_present=None,
                vision_provider_secret_required=self.project_config.providers.vision.enabled,
                vision_provider_api_key_present=None,
                tavily_secret_required=self.project_config.providers.tooling.enabled,
                tavily_api_key_present=None,
                bearer_token_present=None,
            )
        openai_present = bool((self.existing_env.known_values.get("OPENAI_API_KEY", "")).strip())
        vision_required = self.project_config.providers.vision.enabled
        vision_present: bool | None = None
        if vision_required:
            vision_present = bool(
                (
                    self.existing_env.known_values.get("VISION_PROVIDER_API_KEY", "")
                    or self.existing_env.legacy_alias_values.get("MISTRAL_API_KEY", "")
                ).strip()
            )
        tavily_required = self.project_config.providers.tooling.enabled
        tavily_present: bool | None = None
        if tavily_required:
            tavily_present = bool((self.existing_env.known_values.get("TAVILY_API_KEY", "")).strip())

        return SecretReadiness(
            openai_api_key_present=openai_present,
            vision_provider_secret_required=vision_required,
            vision_provider_api_key_present=vision_present,
            tavily_secret_required=tavily_required,
            tavily_api_key_present=tavily_present,
            bearer_token_present=bool((self.existing_env.known_values.get("BACKEND_BEARER_TOKEN", "")).strip()),
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

    @property
    def project_config(self) -> ProjectConfig:
        return self.config_session.project_config

    @property
    def derived_from_legacy(self) -> bool:
        return self.config_session.derived_from_legacy

    def active_target(self) -> str | None:
        if self.deploy_state.has_data():
            return GCP_CLOUD_RUN_TARGET
        if self.project_config.deploy.preferred_target == GCP_CLOUD_RUN_TARGET:
            return GCP_CLOUD_RUN_TARGET
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
                runtime_source_derived_from_legacy=False,
            )
        )
    )


def load_inspection_session(cli_context: CLIContext) -> InspectionSession:
    config_session = load_workspace_session(cli_context)
    return InspectionSession(
        config_session=config_session,
        deploy_state=DeployState.from_payload(config_session.remembered_deploy_state),
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
        derived_from_legacy=store_snapshot.derived_from_legacy,
        configured_runtime_source=store_snapshot.configured_runtime_source,
        effective_runtime_source=store_snapshot.effective_runtime_source,
        runtime_source_derived_from_legacy=store_snapshot.runtime_source_derived_from_legacy,
        remembered_deploy_state=store_snapshot.remembered_deploy_state,
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
        "derived_from_legacy": session.derived_from_legacy,
        "configured_runtime_source": session.configured_runtime_source,
        "effective_runtime_source": session.effective_runtime_source,
        "runtime_source_derived_from_legacy": session.runtime_source_derived_from_legacy,
        "remembered_deploy_state": session.remembered_deploy_state,
        "workspace_resolution_source": session.workspace_resolution_source,
        "active_workspace_root": session.active_workspace_root,
    }


def _strip(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None
