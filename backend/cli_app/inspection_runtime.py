from __future__ import annotations

from dataclasses import dataclass

from backend.cli_app.config_runtime import ConfigSession, load_config_session
from backend.cli_app.context import CLIContext
from backend.cli_app.deploy_state import DeployState
from backend.cli_app.project_config import GCP_CLOUD_RUN_TARGET, ProjectConfig


@dataclass(frozen=True, slots=True)
class InspectionSession:
    config_session: ConfigSession
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


def load_inspection_session(cli_context: CLIContext) -> InspectionSession:
    config_session = load_config_session(cli_context)
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


def _strip(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None
