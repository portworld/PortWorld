from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import secrets
from typing import Any

import click

from portworld_cli.context import CLIContext
from portworld_cli.envfile import (
    EnvFileParseError,
    EnvTemplate,
    EnvWriteResult,
    ParsedEnvFile,
    load_env_template,
    parse_env_file,
    write_canonical_env,
)
from portworld_cli.output import CommandResult, format_key_value_lines
from portworld_cli.paths import ProjectPaths, ProjectRootResolutionError, WorkspacePaths
from portworld_cli.published_workspace import load_published_env_template
from portworld_cli.project_config import (
    CLOUD_PROVIDER_GCP,
    DEFAULT_BACKEND_PROFILE,
    GCP_CLOUD_RUN_TARGET,
    PROJECT_MODE_LOCAL,
    PROJECT_MODE_MANAGED,
    RUNTIME_SOURCE_PUBLISHED,
    RUNTIME_SOURCE_SOURCE,
    GCPCloudRunConfig,
    ProjectConfig,
    ProjectConfigError,
    SecurityConfig,
    ToolingConfig,
    VisionProviderConfig,
    build_env_overrides_from_project_config,
    derive_project_config,
    load_project_config_record,
    write_project_config,
)
from portworld_cli.state import CLIStateDecodeError, CLIStateTypeError, read_json_state


class ConfigRuntimeError(RuntimeError):
    """Base error for config UX runtime failures."""


class ConfigUsageError(ConfigRuntimeError):
    """Raised when config command flags or state are invalid."""


class ConfigValidationError(ConfigRuntimeError):
    """Raised when required config values are missing."""


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
class ConfigSession:
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
class ProviderEditOptions:
    with_vision: bool
    without_vision: bool
    with_tooling: bool
    without_tooling: bool
    openai_api_key: str | None
    vision_provider_api_key: str | None
    tavily_api_key: str | None


@dataclass(frozen=True, slots=True)
class SecurityEditOptions:
    backend_profile: str | None
    cors_origins: str | None
    allowed_hosts: str | None
    bearer_token: str | None
    generate_bearer_token: bool
    clear_bearer_token: bool


@dataclass(frozen=True, slots=True)
class CloudEditOptions:
    project_mode: str | None
    runtime_source: str | None
    project: str | None
    region: str | None
    service: str | None
    artifact_repo: str | None
    sql_instance: str | None
    database: str | None
    bucket: str | None
    min_instances: int | None
    max_instances: int | None
    concurrency: int | None
    cpu: str | None
    memory: str | None


@dataclass(frozen=True, slots=True)
class ProviderSectionResult:
    vision_enabled: bool
    tooling_enabled: bool
    openai_api_key: str
    vision_provider_api_key: str
    tavily_api_key: str


@dataclass(frozen=True, slots=True)
class SecuritySectionResult:
    backend_profile: str
    cors_origins: tuple[str, ...]
    allowed_hosts: tuple[str, ...]
    bearer_token: str


@dataclass(frozen=True, slots=True)
class CloudSectionResult:
    project_mode: str
    runtime_source: str
    cloud_provider: str | None
    preferred_target: str | None
    gcp_cloud_run: GCPCloudRunConfig


@dataclass(frozen=True, slots=True)
class ConfigWriteOutcome:
    project_config: ProjectConfig
    secret_readiness: SecretReadiness
    env_write_result: EnvWriteResult | None


def load_config_session(cli_context: CLIContext) -> ConfigSession:
    workspace_paths = cli_context.resolve_workspace_paths()
    project_paths = workspace_paths.source_project_paths
    template = (
        load_env_template(project_paths.env_example_file)
        if project_paths is not None
        else load_published_env_template()
    )
    existing_env = (
        None
        if template is None
        else parse_env_file(
            project_paths.env_file if project_paths is not None else workspace_paths.workspace_env_file,
            template=template,
        )
    )
    remembered_deploy_state = read_json_state(workspace_paths.gcp_cloud_run_state_file)
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
                RUNTIME_SOURCE_SOURCE
                if project_paths is not None
                else RUNTIME_SOURCE_PUBLISHED
            ),
        )
        configured_runtime_source = None
        runtime_source_derived_from_legacy = True
    else:
        configured_runtime_source = project_config.runtime_source
        runtime_source_derived_from_legacy = not loaded_project_config.runtime_source_explicit
    effective_runtime_source = configured_runtime_source or (
        RUNTIME_SOURCE_SOURCE if project_paths is not None else RUNTIME_SOURCE_PUBLISHED
    )
    return ConfigSession(
        cli_context=cli_context,
        workspace_paths=workspace_paths,
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


def ensure_source_runtime_session(
    session: ConfigSession,
    *,
    command_name: str,
    requested_runtime_source: str | None = None,
) -> ConfigSession:
    if requested_runtime_source == RUNTIME_SOURCE_PUBLISHED:
        raise ConfigUsageError(
            f"{command_name} is not supported when runtime_source=published yet. "
            "Use `portworld config edit cloud --runtime-source published` to record published mode, "
            "and switch back with `portworld config edit cloud --runtime-source source` when you need source-backed commands."
        )

    effective_runtime_source = requested_runtime_source or session.effective_runtime_source
    if effective_runtime_source == RUNTIME_SOURCE_PUBLISHED:
        raise ConfigUsageError(
            f"{command_name} is not supported when runtime_source=published yet. "
            "Switch back with `portworld config edit cloud --runtime-source source`."
        )

    if session.project_paths is None or session.template is None or session.existing_env is None:
        raise ProjectRootResolutionError(
            f"{command_name} requires a PortWorld source checkout with backend/Dockerfile, "
            "backend/.env.example, and docker-compose.yml."
        )

    if session.project_config.runtime_source == RUNTIME_SOURCE_SOURCE:
        return session

    return replace(
        session,
        project_config=replace(session.project_config, runtime_source=RUNTIME_SOURCE_SOURCE),
        configured_runtime_source=RUNTIME_SOURCE_SOURCE,
        effective_runtime_source=RUNTIME_SOURCE_SOURCE,
        runtime_source_derived_from_legacy=False,
    )


def run_config_show(cli_context: CLIContext) -> CommandResult:
    try:
        session = load_config_session(cli_context)
    except (
        ProjectRootResolutionError,
        CLIStateDecodeError,
        CLIStateTypeError,
        EnvFileParseError,
        ProjectConfigError,
    ) as exc:
        return _failure_result("portworld config show", exc, exit_code=2)

    secret_readiness = session.secret_readiness()
    config_payload = session.project_config.to_payload()
    published_runtime_payload = (
        session.project_config.deploy.published_runtime.to_payload()
        if session.effective_runtime_source == RUNTIME_SOURCE_PUBLISHED
        else None
    )
    message = _build_config_show_message(
        workspace_root=session.workspace_root,
        project_config=session.project_config,
        secret_readiness=secret_readiness,
        project_root=(
            None if session.project_paths is None else session.project_paths.project_root
        ),
        env_path=session.env_path,
        derived_from_legacy=session.derived_from_legacy,
        configured_runtime_source=session.configured_runtime_source,
        effective_runtime_source=session.effective_runtime_source,
        runtime_source_derived_from_legacy=session.runtime_source_derived_from_legacy,
    )
    return CommandResult(
        ok=True,
        command="portworld config show",
        message=message,
        data={
            "workspace_root": str(session.workspace_root),
            "project_root": (
                None
                if session.project_paths is None
                else str(session.project_paths.project_root)
            ),
            "project_config_path": str(session.workspace_paths.project_config_file),
            "env_path": None if session.env_path is None else str(session.env_path),
            "compose_path": str(session.workspace_paths.compose_file),
            "project_config": config_payload,
            "secret_readiness": secret_readiness.to_dict(),
            "derived_from_legacy": session.derived_from_legacy,
            "configured_runtime_source": session.configured_runtime_source,
            "effective_runtime_source": session.effective_runtime_source,
            "runtime_source_derived_from_legacy": session.runtime_source_derived_from_legacy,
            "published_runtime": published_runtime_payload,
        },
        exit_code=0,
    )


def run_edit_providers(cli_context: CLIContext, options: ProviderEditOptions) -> CommandResult:
    return _run_section_edit(
        cli_context,
        command_name="portworld config edit providers",
        section_name="providers",
        edit_callback=lambda session: _apply_provider_edit(session, options),
    )


def run_edit_security(cli_context: CLIContext, options: SecurityEditOptions) -> CommandResult:
    return _run_section_edit(
        cli_context,
        command_name="portworld config edit security",
        section_name="security",
        edit_callback=lambda session: _apply_security_edit(session, options),
    )


def run_edit_cloud(cli_context: CLIContext, options: CloudEditOptions) -> CommandResult:
    return _run_section_edit(
        cli_context,
        command_name="portworld config edit cloud",
        section_name="cloud",
        edit_callback=lambda session: _apply_cloud_edit(
            session,
            options,
            prompt_defaults_when_local=True,
        ),
    )


def collect_provider_section(
    session: ConfigSession,
    options: ProviderEditOptions,
) -> ProviderSectionResult:
    _validate_provider_flag_conflicts(options)

    openai_api_key = _resolve_secret_value(
        session.cli_context,
        label="OpenAI API key",
        existing_value=session.existing_env.known_values.get("OPENAI_API_KEY", ""),
        explicit_value=options.openai_api_key,
        required=True,
    )
    vision_enabled = _resolve_toggle(
        session.cli_context,
        prompt="Enable visual memory?",
        current_value=session.project_config.providers.vision.enabled,
        explicit_enable=options.with_vision,
        explicit_disable=options.without_vision,
    )
    vision_provider_api_key = ""
    if vision_enabled:
        if not session.cli_context.non_interactive:
            click.echo(
                f"Visual memory provider: {session.project_config.providers.vision.provider}"
            )
        vision_provider_api_key = _resolve_secret_value(
            session.cli_context,
            label="Vision provider API key",
            existing_value=(
                session.existing_env.known_values.get("VISION_PROVIDER_API_KEY", "")
                or session.existing_env.legacy_alias_values.get("MISTRAL_API_KEY", "")
            ),
            explicit_value=options.vision_provider_api_key,
            required=True,
        )

    tooling_enabled = _resolve_toggle(
        session.cli_context,
        prompt="Enable realtime tooling?",
        current_value=session.project_config.providers.tooling.enabled,
        explicit_enable=options.with_tooling,
        explicit_disable=options.without_tooling,
    )
    tavily_api_key = ""
    if tooling_enabled:
        if not session.cli_context.non_interactive:
            click.echo(
                "Web search provider: "
                f"{session.project_config.providers.tooling.web_search_provider}"
            )
        tavily_api_key = _resolve_secret_value(
            session.cli_context,
            label="Tavily API key (optional)",
            existing_value=session.existing_env.known_values.get("TAVILY_API_KEY", ""),
            explicit_value=options.tavily_api_key,
            required=False,
        )

    return ProviderSectionResult(
        vision_enabled=vision_enabled,
        tooling_enabled=tooling_enabled,
        openai_api_key=openai_api_key,
        vision_provider_api_key=vision_provider_api_key,
        tavily_api_key=tavily_api_key,
    )


def collect_security_section(
    session: ConfigSession,
    options: SecurityEditOptions,
) -> SecuritySectionResult:
    _validate_security_flag_conflicts(options)

    current_profile = _normalize_backend_profile(session.project_config.security.backend_profile)
    backend_profile = _resolve_choice_value(
        session.cli_context,
        prompt="Backend profile",
        current_value=current_profile,
        explicit_value=_normalize_backend_profile(options.backend_profile)
        if options.backend_profile is not None
        else None,
        choices=("development", "production"),
    )
    cors_origins = _resolve_csv_value(
        session.cli_context,
        prompt="CORS origins (comma-separated)",
        current_values=session.project_config.security.cors_origins,
        explicit_value=options.cors_origins,
    )
    allowed_hosts = _resolve_csv_value(
        session.cli_context,
        prompt="Allowed hosts (comma-separated)",
        current_values=session.project_config.security.allowed_hosts,
        explicit_value=options.allowed_hosts,
    )
    bearer_token = _resolve_bearer_token(
        session.cli_context,
        existing_value=session.existing_env.known_values.get("BACKEND_BEARER_TOKEN", ""),
        explicit_value=options.bearer_token,
        generate=options.generate_bearer_token,
        clear=options.clear_bearer_token,
    )
    return SecuritySectionResult(
        backend_profile=backend_profile,
        cors_origins=cors_origins,
        allowed_hosts=allowed_hosts,
        bearer_token=bearer_token,
    )


def collect_cloud_section(
    session: ConfigSession,
    options: CloudEditOptions,
    *,
    prompt_defaults_when_local: bool,
) -> CloudSectionResult:
    current_mode = session.project_config.project_mode
    current_runtime_source = session.effective_runtime_source
    project_mode = _resolve_choice_value(
        session.cli_context,
        prompt="Project mode",
        current_value=current_mode,
        explicit_value=options.project_mode,
        choices=(PROJECT_MODE_LOCAL, PROJECT_MODE_MANAGED),
    )
    runtime_source = _resolve_choice_value(
        session.cli_context,
        prompt="Runtime source",
        current_value=current_runtime_source,
        explicit_value=options.runtime_source,
        choices=(RUNTIME_SOURCE_SOURCE, RUNTIME_SOURCE_PUBLISHED),
    )

    current_gcp = session.project_config.deploy.gcp_cloud_run
    explicit_cloud_change = any(
        value is not None
        for value in (
            options.project,
            options.region,
            options.service,
            options.artifact_repo,
            options.sql_instance,
            options.database,
            options.bucket,
            options.min_instances,
            options.max_instances,
            options.concurrency,
            options.cpu,
            options.memory,
        )
    )
    collect_defaults = (
        project_mode == PROJECT_MODE_MANAGED
        or prompt_defaults_when_local
        or explicit_cloud_change
    )

    gcp_cloud_run = current_gcp
    if collect_defaults:
        project_id = _resolve_optional_text_value(
            session.cli_context,
            prompt="GCP project id",
            current_value=current_gcp.project_id,
            explicit_value=options.project,
        )
        region = _resolve_optional_text_value(
            session.cli_context,
            prompt="Cloud Run region",
            current_value=current_gcp.region,
            explicit_value=options.region,
        )
        service_name = _resolve_required_text_value(
            session.cli_context,
            prompt="Cloud Run service name",
            current_value=current_gcp.service_name,
            explicit_value=options.service,
        )
        artifact_repository = _resolve_required_text_value(
            session.cli_context,
            prompt="Artifact Registry repository",
            current_value=current_gcp.artifact_repository,
            explicit_value=options.artifact_repo,
        )
        sql_instance_name = _resolve_required_text_value(
            session.cli_context,
            prompt="Cloud SQL instance name",
            current_value=current_gcp.sql_instance_name,
            explicit_value=options.sql_instance,
        )
        database_name = _resolve_required_text_value(
            session.cli_context,
            prompt="Cloud SQL database name",
            current_value=current_gcp.database_name,
            explicit_value=options.database,
        )
        bucket_name = _resolve_optional_text_value(
            session.cli_context,
            prompt="GCS bucket name",
            current_value=current_gcp.bucket_name,
            explicit_value=options.bucket,
        )
        min_instances = _resolve_int_value(
            session.cli_context,
            prompt="Minimum Cloud Run instances",
            current_value=current_gcp.min_instances,
            explicit_value=options.min_instances,
        )
        max_instances = _resolve_int_value(
            session.cli_context,
            prompt="Maximum Cloud Run instances",
            current_value=current_gcp.max_instances,
            explicit_value=options.max_instances,
        )
        concurrency = _resolve_int_value(
            session.cli_context,
            prompt="Cloud Run concurrency",
            current_value=current_gcp.concurrency,
            explicit_value=options.concurrency,
        )
        cpu = _resolve_required_text_value(
            session.cli_context,
            prompt="Cloud Run CPU",
            current_value=current_gcp.cpu,
            explicit_value=options.cpu,
        )
        memory = _resolve_required_text_value(
            session.cli_context,
            prompt="Cloud Run memory",
            current_value=current_gcp.memory,
            explicit_value=options.memory,
        )
        if min_instances < 0:
            raise ConfigValidationError("--min-instances must be >= 0.")
        if max_instances < 1:
            raise ConfigValidationError("--max-instances must be >= 1.")
        if min_instances > max_instances:
            raise ConfigValidationError("--min-instances cannot exceed --max-instances.")
        if concurrency < 1:
            raise ConfigValidationError("--concurrency must be >= 1.")
        gcp_cloud_run = GCPCloudRunConfig(
            project_id=project_id,
            region=region,
            service_name=service_name,
            artifact_repository=artifact_repository,
            sql_instance_name=sql_instance_name,
            database_name=database_name,
            bucket_name=bucket_name,
            min_instances=min_instances,
            max_instances=max_instances,
            concurrency=concurrency,
            cpu=cpu,
            memory=memory,
        )

    if project_mode == PROJECT_MODE_MANAGED:
        cloud_provider = CLOUD_PROVIDER_GCP
        preferred_target = GCP_CLOUD_RUN_TARGET
    else:
        cloud_provider = None
        preferred_target = None

    return CloudSectionResult(
        project_mode=project_mode,
        runtime_source=runtime_source,
        cloud_provider=cloud_provider,
        preferred_target=preferred_target,
        gcp_cloud_run=gcp_cloud_run,
    )


def apply_provider_section(
    project_config: ProjectConfig,
    result: ProviderSectionResult,
) -> tuple[ProjectConfig, dict[str, str]]:
    updated_project_config = ProjectConfig(
        schema_version=project_config.schema_version,
        project_mode=project_config.project_mode,
        runtime_source=project_config.runtime_source,
        cloud_provider=project_config.cloud_provider,
        providers=type(project_config.providers)(
            realtime=project_config.providers.realtime,
            vision=VisionProviderConfig(
                enabled=result.vision_enabled,
                provider=project_config.providers.vision.provider,
            ),
            tooling=ToolingConfig(
                enabled=result.tooling_enabled,
                web_search_provider=project_config.providers.tooling.web_search_provider,
            ),
        ),
        security=project_config.security,
        deploy=project_config.deploy,
    )
    env_updates = {
        "OPENAI_API_KEY": result.openai_api_key,
        "VISION_PROVIDER_API_KEY": result.vision_provider_api_key if result.vision_enabled else "",
        "TAVILY_API_KEY": result.tavily_api_key if result.tooling_enabled else "",
    }
    return updated_project_config, env_updates


def apply_security_section(
    project_config: ProjectConfig,
    result: SecuritySectionResult,
) -> tuple[ProjectConfig, dict[str, str]]:
    updated_project_config = ProjectConfig(
        schema_version=project_config.schema_version,
        project_mode=project_config.project_mode,
        runtime_source=project_config.runtime_source,
        cloud_provider=project_config.cloud_provider,
        providers=project_config.providers,
        security=SecurityConfig(
            backend_profile=result.backend_profile,
            cors_origins=result.cors_origins,
            allowed_hosts=result.allowed_hosts,
        ),
        deploy=project_config.deploy,
    )
    return updated_project_config, {"BACKEND_BEARER_TOKEN": result.bearer_token}


def apply_cloud_section(
    project_config: ProjectConfig,
    result: CloudSectionResult,
) -> tuple[ProjectConfig, dict[str, str]]:
    updated_project_config = ProjectConfig(
        schema_version=project_config.schema_version,
        project_mode=result.project_mode,
        runtime_source=result.runtime_source,
        cloud_provider=result.cloud_provider,
        providers=project_config.providers,
        security=project_config.security,
        deploy=type(project_config.deploy)(
            preferred_target=result.preferred_target,
            gcp_cloud_run=result.gcp_cloud_run,
        ),
    )
    return updated_project_config, {}


def write_config_artifacts(
    session: ConfigSession,
    project_config: ProjectConfig,
    env_updates: dict[str, str],
) -> ConfigWriteOutcome:
    env_overrides = build_env_overrides_from_project_config(project_config)
    env_overrides.update(env_updates)
    write_project_config(session.workspace_paths.project_config_file, project_config)
    env_write_result: EnvWriteResult | None = None
    existing_env = session.existing_env
    if session.project_paths is not None and session.template is not None and session.existing_env is not None:
        env_write_result = write_canonical_env(
            session.project_paths.env_file,
            template=session.template,
            existing_env=session.existing_env,
            overrides=env_overrides,
        )
        existing_env = parse_env_file(session.project_paths.env_file, template=session.template)
    updated_session = ConfigSession(
        cli_context=session.cli_context,
        workspace_paths=session.workspace_paths,
        project_paths=session.project_paths,
        template=session.template,
        existing_env=existing_env,
        project_config=project_config,
        derived_from_legacy=False,
        configured_runtime_source=project_config.runtime_source,
        effective_runtime_source=project_config.runtime_source or session.effective_runtime_source,
        runtime_source_derived_from_legacy=False,
        remembered_deploy_state=session.remembered_deploy_state,
    )
    return ConfigWriteOutcome(
        project_config=project_config,
        secret_readiness=updated_session.secret_readiness(),
        env_write_result=env_write_result,
    )


def confirm_apply(
    cli_context: CLIContext,
    *,
    command_name: str,
    env_path: Path | None,
    project_config_path: Path,
    summary_lines: tuple[str, ...],
    force: bool = False,
) -> None:
    if cli_context.non_interactive:
        if env_path is not None and env_path.exists() and not force and not cli_context.yes:
            raise ConfigUsageError(
                "backend/.env already exists. Re-run with --force or --yes in non-interactive mode."
            )
        return
    if cli_context.yes:
        return
    prompt_lines = [
        f"Apply {command_name} changes?",
        f"project_config_path: {project_config_path}",
        *summary_lines,
    ]
    if env_path is not None:
        prompt_lines.insert(2, f"env_path: {env_path}")
    confirmed = click.confirm("\n".join(prompt_lines), default=True, show_default=True)
    if not confirmed:
        raise click.Abort()


def build_section_success_message(
    *,
    section_name: str,
    project_config: ProjectConfig,
    secret_readiness: SecretReadiness,
    env_path: Path | None,
    project_config_path: Path,
    backup_path: Path | None,
) -> str:
    lines = [
        f"section: {section_name}",
        f"project_mode: {project_config.project_mode}",
        f"runtime_source: {project_config.runtime_source or 'unset'}",
        f"cloud_provider: {project_config.cloud_provider or 'none'}",
        f"vision_memory: {'yes' if project_config.providers.vision.enabled else 'no'}",
        f"realtime_tooling: {'yes' if project_config.providers.tooling.enabled else 'no'}",
        f"backend_profile: {_normalize_backend_profile(project_config.security.backend_profile)}",
        f"project_config_path: {project_config_path}",
        f"openai_api_key: {_presence_label(secret_readiness.openai_api_key_present)}",
        f"vision_provider_api_key: {_required_presence_label(secret_readiness.vision_provider_secret_required, secret_readiness.vision_provider_api_key_present)}",
        f"tavily_api_key: {_required_presence_label(secret_readiness.tavily_secret_required, secret_readiness.tavily_api_key_present)}",
        f"bearer_token: {_presence_label(secret_readiness.bearer_token_present)}",
    ]
    if env_path is not None:
        lines.insert(7, f"env_path: {env_path}")
    if backup_path is not None:
        lines.append(f"backup_path: {backup_path}")
    return "\n".join(lines)


def build_init_review_lines(
    *,
    project_config: ProjectConfig,
    secret_readiness: SecretReadiness,
) -> tuple[str, ...]:
    return (
        f"project_mode: {project_config.project_mode}",
        f"runtime_source: {project_config.runtime_source or 'unset'}",
        f"cloud_provider: {project_config.cloud_provider or 'none'}",
        f"vision_memory: {'yes' if project_config.providers.vision.enabled else 'no'}",
        f"realtime_tooling: {'yes' if project_config.providers.tooling.enabled else 'no'}",
        f"backend_profile: {_normalize_backend_profile(project_config.security.backend_profile)}",
        f"cors_origins: {','.join(project_config.security.cors_origins)}",
        f"allowed_hosts: {','.join(project_config.security.allowed_hosts)}",
        f"gcp_project_id: {project_config.deploy.gcp_cloud_run.project_id or 'unset'}",
        f"gcp_region: {project_config.deploy.gcp_cloud_run.region or 'unset'}",
        f"service_name: {project_config.deploy.gcp_cloud_run.service_name}",
        f"openai_api_key: {_presence_label(secret_readiness.openai_api_key_present)}",
        f"vision_provider_api_key: {_required_presence_label(secret_readiness.vision_provider_secret_required, secret_readiness.vision_provider_api_key_present)}",
        f"tavily_api_key: {_required_presence_label(secret_readiness.tavily_secret_required, secret_readiness.tavily_api_key_present)}",
        f"bearer_token: {_presence_label(secret_readiness.bearer_token_present)}",
    )


def build_init_success_message(
    *,
    project_config: ProjectConfig,
    secret_readiness: SecretReadiness,
    env_path: Path | None,
    project_config_path: Path,
    backup_path: Path | None,
) -> str:
    lines = list(
        build_init_review_lines(
            project_config=project_config,
            secret_readiness=secret_readiness,
        )
    )
    lines.extend(
        [
            f"project_config_path: {project_config_path}",
        ]
    )
    if env_path is not None:
        lines.append(f"env_path: {env_path}")
    if backup_path is not None:
        lines.append(f"backup_path: {backup_path}")
    lines.extend(
        [
            "next: portworld doctor --target local",
            "next: portworld config show",
            "next: portworld deploy gcp-cloud-run",
        ]
    )
    return "\n".join(lines)


def preview_secret_readiness(
    session: ConfigSession,
    project_config: ProjectConfig,
    env_updates: dict[str, str],
) -> SecretReadiness:
    return _secret_readiness_with_updates(session, project_config, env_updates)


def _run_section_edit(
    cli_context: CLIContext,
    *,
    command_name: str,
    section_name: str,
    edit_callback,
) -> CommandResult:
    try:
        session = load_config_session(cli_context)
        updated_project_config, env_updates, review_lines = edit_callback(session)
        confirm_apply(
            cli_context,
            command_name=command_name,
            env_path=session.env_path,
            project_config_path=session.workspace_paths.project_config_file,
            summary_lines=review_lines,
        )
        outcome = write_config_artifacts(session, updated_project_config, env_updates)
    except ProjectRootResolutionError as exc:
        return _failure_result(command_name, exc, exit_code=1)
    except (
        CLIStateDecodeError,
        CLIStateTypeError,
        ConfigRuntimeError,
        EnvFileParseError,
        ProjectConfigError,
    ) as exc:
        return _failure_result(command_name, exc, exit_code=2)
    except click.Abort:
        return CommandResult(
            ok=False,
            command=command_name,
            message="Aborted before configuration changes were applied.",
            data={"status": "aborted", "error_type": "Abort"},
            exit_code=1,
        )

    return CommandResult(
        ok=True,
        command=command_name,
        message=build_section_success_message(
            section_name=section_name,
            project_config=outcome.project_config,
            secret_readiness=outcome.secret_readiness,
            env_path=None if outcome.env_write_result is None else outcome.env_write_result.env_path,
            project_config_path=session.workspace_paths.project_config_file,
            backup_path=None if outcome.env_write_result is None else outcome.env_write_result.backup_path,
        ),
        data={
            "workspace_root": str(session.workspace_root),
            "project_root": (
                None
                if session.project_paths is None
                else str(session.project_paths.project_root)
            ),
            "project_config_path": str(session.workspace_paths.project_config_file),
            "env_path": (
                None
                if outcome.env_write_result is None
                else str(outcome.env_write_result.env_path)
            ),
            "backup_path": (
                str(outcome.env_write_result.backup_path)
                if outcome.env_write_result is not None and outcome.env_write_result.backup_path
                else None
            ),
            "project_config": outcome.project_config.to_payload(),
            "secret_readiness": outcome.secret_readiness.to_dict(),
            "updated_section": section_name,
            "configured_runtime_source": outcome.project_config.runtime_source,
            "effective_runtime_source": outcome.project_config.runtime_source,
        },
        exit_code=0,
    )


def _apply_provider_edit(
    session: ConfigSession,
    options: ProviderEditOptions,
) -> tuple[ProjectConfig, dict[str, str], tuple[str, ...]]:
    session = ensure_source_runtime_session(
        session,
        command_name="portworld config edit providers",
    )
    provider_result = collect_provider_section(session, options)
    updated_project_config, env_updates = apply_provider_section(
        session.project_config,
        provider_result,
    )
    preview_readiness = _secret_readiness_with_updates(
        session,
        updated_project_config,
        env_updates,
    )
    return updated_project_config, env_updates, build_init_review_lines(
        project_config=updated_project_config,
        secret_readiness=preview_readiness,
    )


def _apply_security_edit(
    session: ConfigSession,
    options: SecurityEditOptions,
) -> tuple[ProjectConfig, dict[str, str], tuple[str, ...]]:
    session = ensure_source_runtime_session(
        session,
        command_name="portworld config edit security",
    )
    security_result = collect_security_section(session, options)
    updated_project_config, env_updates = apply_security_section(
        session.project_config,
        security_result,
    )
    preview_readiness = _secret_readiness_with_updates(
        session,
        updated_project_config,
        env_updates,
    )
    return updated_project_config, env_updates, build_init_review_lines(
        project_config=updated_project_config,
        secret_readiness=preview_readiness,
    )


def _apply_cloud_edit(
    session: ConfigSession,
    options: CloudEditOptions,
    *,
    prompt_defaults_when_local: bool,
) -> tuple[ProjectConfig, dict[str, str], tuple[str, ...]]:
    cloud_result = collect_cloud_section(
        session,
        options,
        prompt_defaults_when_local=prompt_defaults_when_local,
    )
    updated_project_config, env_updates = apply_cloud_section(
        session.project_config,
        cloud_result,
    )
    preview_readiness = _secret_readiness_with_updates(
        session,
        updated_project_config,
        env_updates,
    )
    return updated_project_config, env_updates, build_init_review_lines(
        project_config=updated_project_config,
        secret_readiness=preview_readiness,
    )


def _build_config_show_message(
    *,
    workspace_root: Path,
    project_config: ProjectConfig,
    secret_readiness: SecretReadiness,
    project_root: Path | None,
    env_path: Path | None,
    derived_from_legacy: bool,
    configured_runtime_source: str | None,
    effective_runtime_source: str,
    runtime_source_derived_from_legacy: bool,
) -> str:
    pairs: list[tuple[str, object | None]] = [
        ("workspace_root", workspace_root),
        ("project_root", project_root),
        ("project_mode", project_config.project_mode),
        ("runtime_source", project_config.runtime_source or "unset"),
        ("configured_runtime_source", configured_runtime_source or "legacy_default"),
        ("effective_runtime_source", effective_runtime_source),
        ("runtime_source_derived_from_legacy", runtime_source_derived_from_legacy),
        ("cloud_provider", project_config.cloud_provider or "none"),
        ("realtime_provider", project_config.providers.realtime.provider),
        ("vision_memory", project_config.providers.vision.enabled),
        ("vision_provider", project_config.providers.vision.provider),
        ("realtime_tooling", project_config.providers.tooling.enabled),
        ("web_search_provider", project_config.providers.tooling.web_search_provider),
        ("backend_profile", _normalize_backend_profile(project_config.security.backend_profile)),
        ("cors_origins", ",".join(project_config.security.cors_origins)),
        ("allowed_hosts", ",".join(project_config.security.allowed_hosts)),
        ("preferred_target", project_config.deploy.preferred_target or "none"),
        ("gcp_project_id", project_config.deploy.gcp_cloud_run.project_id or "unset"),
        ("gcp_region", project_config.deploy.gcp_cloud_run.region or "unset"),
        ("gcp_service_name", project_config.deploy.gcp_cloud_run.service_name),
        ("env_path", env_path),
        ("derived_from_legacy", derived_from_legacy),
        ("openai_api_key", _presence_label(secret_readiness.openai_api_key_present)),
        (
            "vision_provider_api_key",
            _required_presence_label(
                secret_readiness.vision_provider_secret_required,
                secret_readiness.vision_provider_api_key_present,
            ),
        ),
        (
            "tavily_api_key",
            _required_presence_label(
                secret_readiness.tavily_secret_required,
                secret_readiness.tavily_api_key_present,
            ),
        ),
        ("bearer_token", _presence_label(secret_readiness.bearer_token_present)),
    ]
    if effective_runtime_source == RUNTIME_SOURCE_PUBLISHED:
        pairs[20:20] = [
            ("published_release_tag", project_config.deploy.published_runtime.release_tag or "unset"),
            ("published_image_ref", project_config.deploy.published_runtime.image_ref or "unset"),
            ("published_host_port", project_config.deploy.published_runtime.host_port),
            ("compose_path", workspace_root / "docker-compose.yml"),
        ]
    return format_key_value_lines(*pairs)


def _secret_readiness_with_updates(
    session: ConfigSession,
    project_config: ProjectConfig,
    env_updates: dict[str, str],
) -> SecretReadiness:
    if session.existing_env is None:
        return SecretReadiness(
            openai_api_key_present=None,
            vision_provider_secret_required=project_config.providers.vision.enabled,
            vision_provider_api_key_present=None,
            tavily_secret_required=project_config.providers.tooling.enabled,
            tavily_api_key_present=None,
            bearer_token_present=None,
        )
    known_values = dict(session.existing_env.known_values)
    known_values.update(env_updates)

    def _known(key: str) -> str:
        return str(known_values.get(key, "")).strip()

    vision_required = project_config.providers.vision.enabled
    vision_present: bool | None = None
    if vision_required:
        vision_present = bool(_known("VISION_PROVIDER_API_KEY") or session.existing_env.legacy_alias_values.get("MISTRAL_API_KEY", "").strip())

    tavily_required = project_config.providers.tooling.enabled
    tavily_present: bool | None = None
    if tavily_required:
        tavily_present = bool(_known("TAVILY_API_KEY"))

    return SecretReadiness(
        openai_api_key_present=bool(_known("OPENAI_API_KEY")),
        vision_provider_secret_required=vision_required,
        vision_provider_api_key_present=vision_present,
        tavily_secret_required=tavily_required,
        tavily_api_key_present=tavily_present,
        bearer_token_present=bool(_known("BACKEND_BEARER_TOKEN")),
    )


def _resolve_toggle(
    cli_context: CLIContext,
    *,
    prompt: str,
    current_value: bool,
    explicit_enable: bool,
    explicit_disable: bool,
) -> bool:
    if explicit_enable:
        return True
    if explicit_disable:
        return False
    if cli_context.non_interactive:
        return current_value
    return bool(click.confirm(prompt, default=current_value, show_default=True))


def _resolve_secret_value(
    cli_context: CLIContext,
    *,
    label: str,
    existing_value: str,
    explicit_value: str | None,
    required: bool,
) -> str:
    if explicit_value is not None:
        value = explicit_value.strip()
        if required and not value:
            raise ConfigValidationError(f"{label} is required.")
        return value

    current_value = existing_value.strip()
    if cli_context.non_interactive:
        if required and not current_value:
            raise ConfigValidationError(f"{label} is required in non-interactive mode.")
        return current_value

    if current_value:
        click.echo(f"{label}: existing value detected.")
    while True:
        prompt_text = (
            f"{label} (press Enter to keep the existing value)"
            if current_value
            else label
        )
        response = click.prompt(
            prompt_text,
            default="",
            show_default=False,
            hide_input=True,
        ).strip()
        if response:
            return response
        if current_value:
            return current_value
        if not required:
            return ""
        click.echo(f"{label} is required.", err=True)


def _resolve_bearer_token(
    cli_context: CLIContext,
    *,
    existing_value: str,
    explicit_value: str | None,
    generate: bool,
    clear: bool,
) -> str:
    if explicit_value is not None and (generate or clear):
        raise ConfigUsageError(
            "Use only one of --bearer-token, --generate-bearer-token, or --clear-bearer-token."
        )
    if generate and clear:
        raise ConfigUsageError(
            "Use only one of --generate-bearer-token or --clear-bearer-token."
        )
    if explicit_value is not None:
        value = explicit_value.strip()
        if not value:
            raise ConfigValidationError("Bearer token cannot be empty. Use --clear-bearer-token instead.")
        return value
    if clear:
        return ""
    if generate:
        return secrets.token_hex(32)

    current_value = existing_value.strip()
    if cli_context.non_interactive:
        return current_value

    if current_value:
        action = click.prompt(
            "Bearer token action",
            type=click.Choice(["keep", "generate", "replace", "clear"]),
            default="keep",
            show_default=True,
        )
        if action == "keep":
            return current_value
        if action == "generate":
            return secrets.token_hex(32)
        if action == "clear":
            return ""
        return _resolve_secret_value(
            cli_context,
            label="Bearer token",
            existing_value=current_value,
            explicit_value=None,
            required=True,
        )

    should_generate = click.confirm(
        "Generate a local bearer token for development?",
        default=False,
        show_default=True,
    )
    if should_generate:
        return secrets.token_hex(32)
    return _resolve_secret_value(
        cli_context,
        label="Bearer token (optional)",
        existing_value="",
        explicit_value=None,
        required=False,
    )


def _resolve_choice_value(
    cli_context: CLIContext,
    *,
    prompt: str,
    current_value: str,
    explicit_value: str | None,
    choices: tuple[str, ...],
) -> str:
    if explicit_value is not None:
        normalized = explicit_value.strip().lower()
        if normalized not in choices:
            allowed = ", ".join(choices)
            raise ConfigValidationError(f"{prompt} must be one of: {allowed}.")
        return normalized
    if cli_context.non_interactive:
        return current_value
    return click.prompt(
        prompt,
        type=click.Choice(choices),
        default=current_value,
        show_default=True,
    )


def _resolve_csv_value(
    cli_context: CLIContext,
    *,
    prompt: str,
    current_values: tuple[str, ...],
    explicit_value: str | None,
) -> tuple[str, ...]:
    if explicit_value is not None:
        values = _parse_csv_tuple(explicit_value)
        if not values:
            raise ConfigValidationError(f"{prompt} cannot be empty.")
        return values
    if cli_context.non_interactive:
        return current_values
    current_text = ",".join(current_values)
    response = click.prompt(
        prompt,
        default=current_text,
        show_default=True,
    )
    values = _parse_csv_tuple(response)
    if not values:
        raise ConfigValidationError(f"{prompt} cannot be empty.")
    return values


def _resolve_required_text_value(
    cli_context: CLIContext,
    *,
    prompt: str,
    current_value: str,
    explicit_value: str | None,
) -> str:
    if explicit_value is not None:
        value = explicit_value.strip()
        if not value:
            raise ConfigValidationError(f"{prompt} is required.")
        return value
    if cli_context.non_interactive:
        if not current_value.strip():
            raise ConfigValidationError(f"{prompt} is required in non-interactive mode.")
        return current_value.strip()
    response = click.prompt(prompt, default=current_value, show_default=True)
    value = response.strip()
    if not value:
        raise ConfigValidationError(f"{prompt} is required.")
    return value


def _resolve_optional_text_value(
    cli_context: CLIContext,
    *,
    prompt: str,
    current_value: str | None,
    explicit_value: str | None,
) -> str | None:
    if explicit_value is not None:
        value = explicit_value.strip()
        return value or None
    if cli_context.non_interactive:
        return current_value
    response = click.prompt(
        prompt,
        default=current_value or "",
        show_default=bool(current_value),
    )
    value = response.strip()
    return value or None


def _resolve_int_value(
    cli_context: CLIContext,
    *,
    prompt: str,
    current_value: int,
    explicit_value: int | None,
) -> int:
    if explicit_value is not None:
        return explicit_value
    if cli_context.non_interactive:
        return current_value
    return int(
        click.prompt(
            prompt,
            type=int,
            default=current_value,
            show_default=True,
        )
    )


def _validate_provider_flag_conflicts(options: ProviderEditOptions) -> None:
    if options.with_vision and options.without_vision:
        raise ConfigUsageError("Use only one of --with-vision or --without-vision.")
    if options.with_tooling and options.without_tooling:
        raise ConfigUsageError("Use only one of --with-tooling or --without-tooling.")


def _validate_security_flag_conflicts(options: SecurityEditOptions) -> None:
    if options.generate_bearer_token and options.clear_bearer_token:
        raise ConfigUsageError(
            "Use only one of --generate-bearer-token or --clear-bearer-token."
        )


def _normalize_backend_profile(value: str | None) -> str:
    normalized = (value or DEFAULT_BACKEND_PROFILE).strip().lower()
    if normalized in {"prod", "production"}:
        return "production"
    return "development"


def _parse_csv_tuple(raw_value: str) -> tuple[str, ...]:
    values = tuple(part.strip() for part in raw_value.split(",") if part.strip())
    return values


def _presence_label(is_present: bool | None) -> str:
    if is_present is None:
        return "unknown"
    return "present" if is_present else "missing"


def _required_presence_label(required: bool, present: bool | None) -> str:
    if not required:
        return "not_required"
    return "present" if present else "missing"


def _failure_result(command_name: str, exc: Exception, *, exit_code: int) -> CommandResult:
    return CommandResult(
        ok=False,
        command=command_name,
        message=str(exc),
        data={"status": "error", "error_type": type(exc).__name__},
        exit_code=exit_code,
    )
