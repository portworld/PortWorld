from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
import re

from portworld_cli.context import CLIContext
from portworld_cli.deploy_artifacts import (
    IMAGE_NAME,
    IMAGE_SOURCE_MODE_PUBLISHED_RELEASE,
    IMAGE_SOURCE_MODE_SOURCE_BUILD,
    PUBLISHED_ARTIFACT_REPOSITORY_SUFFIX,
    derive_remote_image_name,
)
from portworld_cli.deploy_state import DeployState
from portworld_cli.gcp import GCPAdapters, build_image_uri, resolve_project_id, resolve_region
from portworld_cli.workspace.project_config import (
    DEFAULT_GCP_ARTIFACT_REPOSITORY,
    DEFAULT_GCP_CONCURRENCY,
    DEFAULT_GCP_CPU,
    DEFAULT_GCP_MAX_INSTANCES,
    DEFAULT_GCP_MEMORY,
    DEFAULT_GCP_MIN_INSTANCES,
    DEFAULT_GCP_REGION,
    DEFAULT_GCP_SERVICE_NAME,
    RUNTIME_SOURCE_PUBLISHED,
    RUNTIME_SOURCE_SOURCE,
    ProjectConfig,
)
from portworld_cli.workspace.discovery.paths import ProjectRootResolutionError
from portworld_cli.workspace.session import WorkspaceSession as ConfigSession
from portworld_cli.workspace.session import load_workspace_session

from portworld_cli.deploy.published import resolve_published_image_selection
from portworld_cli.deploy.source import resolve_source_image_tag
from portworld_cli.ux.prompts import prompt_text


DEFAULT_REGION = DEFAULT_GCP_REGION
DEFAULT_SERVICE_NAME = DEFAULT_GCP_SERVICE_NAME
DEFAULT_ARTIFACT_REPOSITORY = DEFAULT_GCP_ARTIFACT_REPOSITORY
DEFAULT_CPU = DEFAULT_GCP_CPU
DEFAULT_MEMORY = DEFAULT_GCP_MEMORY
DEFAULT_MIN_INSTANCES = DEFAULT_GCP_MIN_INSTANCES
DEFAULT_MAX_INSTANCES = DEFAULT_GCP_MAX_INSTANCES
DEFAULT_CONCURRENCY = DEFAULT_GCP_CONCURRENCY
DEFAULT_BUCKET_SUFFIX = "portworld-artifacts"


class DeployUsageError(RuntimeError):
    pass


class DeployStageError(RuntimeError):
    def __init__(self, *, stage: str, message: str, action: str | None = None) -> None:
        super().__init__(message)
        self.stage = stage
        self.action = action


@dataclass(frozen=True, slots=True)
class DeployGCPCloudRunOptions:
    project: str | None = None
    region: str | None = None
    service: str | None = None
    artifact_repo: str | None = None
    bucket: str | None = None
    tag: str | None = None
    min_instances: int | None = None
    max_instances: int | None = None
    concurrency: int | None = None
    cpu: str | None = None
    memory: str | None = None
    sql_instance: str | None = None
    database: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedDeployConfig:
    runtime_source: str
    image_source_mode: str
    project_id: str
    region: str
    service_name: str
    artifact_repository_base: str
    artifact_repository: str
    sql_instance_name: str
    database_name: str
    bucket_name: str
    image_tag: str
    deploy_image_uri: str
    published_release_tag: str | None
    published_image_ref: str | None
    min_instances: int
    max_instances: int
    concurrency: int
    cpu: str
    memory: str


def load_deploy_session(cli_context: CLIContext) -> ConfigSession:
    session = load_workspace_session(cli_context)
    if session.env_path is None or not session.env_path.is_file():
        if session.effective_runtime_source == RUNTIME_SOURCE_PUBLISHED:
            raise DeployStageError(
                stage="repo_config_discovery",
                message="Published workspace .env is missing.",
                action="Run `portworld init --runtime-source published` first.",
            )
        raise DeployStageError(
            stage="repo_config_discovery",
            message="backend/.env is missing.",
            action="Run `portworld init` first.",
        )
    if session.effective_runtime_source == RUNTIME_SOURCE_SOURCE and session.project_paths is None:
        raise ProjectRootResolutionError("Run from a PortWorld repo checkout or pass --project-root.")
    return session


def resolve_deploy_config(
    cli_context: CLIContext,
    *,
    adapters: GCPAdapters,
    env_values: OrderedDict[str, str],
    project_config: ProjectConfig | None,
    remembered_state: DeployState,
    options: DeployGCPCloudRunOptions,
    runtime_source: str,
    project_root: Path | None,
) -> ResolvedDeployConfig:
    gcp_defaults = None if project_config is None else project_config.deploy.gcp_cloud_run
    configured_project = adapters.auth.get_configured_project()
    project_id = resolve_project_id(
        explicit_project_id=options.project,
        project_config_project_id=(None if gcp_defaults is None else gcp_defaults.project_id),
        configured_project_id=configured_project.value if configured_project.ok else None,
        remembered_project_id=remembered_state.project_id,
        allow_remembered=True,
    ).value
    project_id = _prompt_or_require_text(
        cli_context,
        prompt="GCP project id",
        value=project_id,
        error_message="A GCP project id is required. Pass --project or set a gcloud default project.",
    )

    configured_region = adapters.auth.get_configured_run_region()
    region = resolve_region(
        explicit_region=options.region,
        project_config_region=None if gcp_defaults is None else gcp_defaults.region,
        configured_region=configured_region.value if configured_region.ok else None,
        remembered_region=remembered_state.region,
        allow_remembered=True,
        default_region=DEFAULT_REGION,
    ).value
    region = _prompt_or_require_text(
        cli_context,
        prompt="Cloud Run region",
        value=region,
        error_message="A Cloud Run region is required.",
        default=DEFAULT_REGION,
    )

    service_name = _resolve_text_value(
        explicit=options.service,
        remembered=_first_non_empty(
            None if gcp_defaults is None else gcp_defaults.service_name,
            remembered_state.service_name,
        ),
        default=DEFAULT_SERVICE_NAME,
    )
    artifact_repository = _resolve_text_value(
        explicit=options.artifact_repo,
        remembered=_first_non_empty(
            None if gcp_defaults is None else gcp_defaults.artifact_repository,
            _remembered_artifact_repository_base(remembered_state),
        ),
        default=DEFAULT_ARTIFACT_REPOSITORY,
    )
    # Deprecated: kept on the resolved config for compatibility with older reporting surfaces.
    sql_instance_name = _resolve_text_value(
        explicit=options.sql_instance,
        remembered=_first_non_empty(
            None if gcp_defaults is None else gcp_defaults.sql_instance_name,
            remembered_state.cloud_sql_instance,
        ),
        default="",
    )
    database_name = _resolve_text_value(
        explicit=options.database,
        remembered=_first_non_empty(
            None if gcp_defaults is None else gcp_defaults.database_name,
            remembered_state.database_name,
        ),
        default="",
    )
    bucket_name = _resolve_text_value(
        explicit=options.bucket,
        remembered=_first_non_empty(
            None if gcp_defaults is None else gcp_defaults.bucket_name,
            remembered_state.bucket_name,
        ),
        default=_default_bucket_name(project_id),
    )

    published_release_tag = None
    published_image_ref = None
    image_source_mode = IMAGE_SOURCE_MODE_SOURCE_BUILD
    resolved_artifact_repository = artifact_repository
    deploy_image_name = IMAGE_NAME
    if runtime_source == RUNTIME_SOURCE_PUBLISHED:
        published_runtime = None if project_config is None else project_config.deploy.published_runtime
        try:
            published_selection = resolve_published_image_selection(
                explicit_tag=options.tag,
                artifact_repository=artifact_repository,
                release_tag=(None if published_runtime is None else published_runtime.release_tag),
                image_ref=(None if published_runtime is None else published_runtime.image_ref),
            )
        except ValueError as exc:
            raise DeployUsageError(str(exc)) from exc
        published_release_tag = published_selection.release_tag
        published_image_ref = published_selection.image_ref
        image_tag = published_selection.image_tag
        image_source_mode = published_selection.image_source_mode
        resolved_artifact_repository = published_selection.artifact_repository
        deploy_image_name = derive_remote_image_name(
            published_selection.image_ref,
            fallback_image_name=IMAGE_NAME,
        )
    else:
        if project_root is None:
            raise DeployUsageError("Source-mode deploy requires a PortWorld repo checkout.")
        image_tag = resolve_source_image_tag(
            explicit_tag=options.tag,
            project_root=project_root,
        )

    min_instances = (
        options.min_instances
        if options.min_instances is not None
        else (gcp_defaults.min_instances if gcp_defaults is not None else DEFAULT_MIN_INSTANCES)
    )
    max_instances = (
        options.max_instances
        if options.max_instances is not None
        else (gcp_defaults.max_instances if gcp_defaults is not None else DEFAULT_MAX_INSTANCES)
    )
    concurrency = (
        options.concurrency
        if options.concurrency is not None
        else (gcp_defaults.concurrency if gcp_defaults is not None else DEFAULT_CONCURRENCY)
    )
    cpu = _resolve_text_value(
        explicit=options.cpu,
        remembered=None if gcp_defaults is None else gcp_defaults.cpu,
        default=DEFAULT_CPU,
    )
    memory = _resolve_text_value(
        explicit=options.memory,
        remembered=None if gcp_defaults is None else gcp_defaults.memory,
        default=DEFAULT_MEMORY,
    )

    if min_instances < 0:
        raise DeployUsageError("--min-instances must be >= 0.")
    if max_instances < 1:
        raise DeployUsageError("--max-instances must be >= 1.")
    if min_instances > max_instances:
        raise DeployUsageError("--min-instances cannot exceed --max-instances.")
    if concurrency < 1:
        raise DeployUsageError("--concurrency must be >= 1.")

    return ResolvedDeployConfig(
        runtime_source=runtime_source,
        image_source_mode=image_source_mode,
        project_id=project_id,
        region=region,
        service_name=service_name,
        artifact_repository_base=artifact_repository,
        artifact_repository=resolved_artifact_repository,
        sql_instance_name=sql_instance_name,
        database_name=database_name,
        bucket_name=bucket_name,
        image_tag=image_tag,
        deploy_image_uri=build_image_uri(
            project_id=project_id,
            region=region,
            repository=resolved_artifact_repository,
            image_name=deploy_image_name,
            tag=image_tag,
        ),
        published_release_tag=published_release_tag,
        published_image_ref=published_image_ref,
        min_instances=min_instances,
        max_instances=max_instances,
        concurrency=concurrency,
        cpu=cpu,
        memory=memory,
    )


def _resolve_text_value(
    *,
    explicit: str | None,
    remembered: str | None,
    default: str,
) -> str:
    return _first_non_empty(explicit, remembered, default) or default


def _prompt_or_require_text(
    cli_context: CLIContext,
    *,
    prompt: str,
    value: str | None,
    error_message: str,
    default: str | None = None,
) -> str:
    if value is not None and value.strip():
        return value.strip()
    if cli_context.non_interactive:
        raise DeployUsageError(error_message)
    response = prompt_text(
        cli_context,
        message=prompt,
        default=default or "",
        show_default=default is not None,
    )
    normalized = response.strip()
    if not normalized:
        raise DeployUsageError(error_message)
    return normalized


def _first_non_empty(*values: str | None) -> str | None:
    for value in values:
        if value is not None and value.strip():
            return value.strip()
    return None


def _default_bucket_name(project_id: str) -> str:
    candidate = f"{project_id.lower()}-{DEFAULT_BUCKET_SUFFIX}"
    candidate = re.sub(r"[^a-z0-9._-]+", "-", candidate).strip("-.")
    if len(candidate) <= 63:
        return candidate
    return candidate[:63].rstrip("-.")


def _remembered_artifact_repository_base(state: DeployState) -> str | None:
    explicit_base = _first_non_empty(state.artifact_repository_base)
    if explicit_base is not None:
        return explicit_base

    remembered_repo = _first_non_empty(state.artifact_repository)
    if remembered_repo is None:
        return None
    if (
        state.image_source_mode == IMAGE_SOURCE_MODE_PUBLISHED_RELEASE
        and remembered_repo.endswith(PUBLISHED_ARTIFACT_REPOSITORY_SUFFIX)
    ):
        base = remembered_repo[: -len(PUBLISHED_ARTIFACT_REPOSITORY_SUFFIX)].strip()
        if base:
            return base
    return remembered_repo
