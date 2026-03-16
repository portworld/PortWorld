from __future__ import annotations

from dataclasses import dataclass

from portworld_cli.deploy_artifacts import (
    IMAGE_NAME,
    IMAGE_SOURCE_MODE_PUBLISHED_RELEASE,
    derive_published_artifact_repository,
)
from portworld_cli.gcp import (
    GCPAdapters,
    REQUIRED_GCP_SERVICES,
    build_image_uri,
    resolve_project_id,
    resolve_region,
)
from portworld_cli.output import DiagnosticCheck
from portworld_cli.workspace.paths import ProjectPaths
from portworld_cli.workspace.project_config import (
    DEFAULT_GCP_ARTIFACT_REPOSITORY,
    DEFAULT_GCP_REGION,
    RUNTIME_SOURCE_PUBLISHED,
    ProjectConfig,
)
from backend.core.settings import Settings, load_environment_files
from backend.realtime.factory import RealtimeProviderFactory
from backend.tools.runtime import SearchProviderFactory
from backend.vision.factory import VisionAnalyzerFactory


DEFAULT_ARTIFACT_REPOSITORY = DEFAULT_GCP_ARTIFACT_REPOSITORY
DOCTOR_IMAGE_TAG = "doctor-check"
SUGGESTED_DEFAULT_REGION = DEFAULT_GCP_REGION


@dataclass(frozen=True, slots=True)
class GCPDoctorSecretReadiness:
    openai_api_key_present: bool
    vision_provider_secret_required: bool
    vision_provider_api_key_present: bool | None
    tavily_secret_required: bool
    tavily_api_key_present: bool | None
    backend_bearer_token_present: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "openai_api_key_present": self.openai_api_key_present,
            "vision_provider_secret_required": self.vision_provider_secret_required,
            "vision_provider_api_key_present": self.vision_provider_api_key_present,
            "tavily_secret_required": self.tavily_secret_required,
            "tavily_api_key_present": self.tavily_api_key_present,
            "backend_bearer_token_present": self.backend_bearer_token_present,
        }


@dataclass(frozen=True, slots=True)
class GCPDoctorProductionPosture:
    backend_profile: str
    profile_is_production: bool
    cors_origins_explicit: bool
    allowed_hosts_explicit: bool
    debug_trace_disabled: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "backend_profile": self.backend_profile,
            "profile_is_production": self.profile_is_production,
            "cors_origins_explicit": self.cors_origins_explicit,
            "allowed_hosts_explicit": self.allowed_hosts_explicit,
            "debug_trace_disabled": self.debug_trace_disabled,
        }


@dataclass(frozen=True, slots=True)
class GCPDoctorDetails:
    account: str | None
    project_id: str | None
    project_source: str | None
    region: str | None
    region_source: str | None
    image_uri: str | None
    required_apis: tuple[dict[str, object], ...]
    secrets: GCPDoctorSecretReadiness | None
    production_posture: GCPDoctorProductionPosture | None

    def to_dict(self) -> dict[str, object]:
        return {
            "account": self.account,
            "project_id": self.project_id,
            "project_source": self.project_source,
            "region": self.region,
            "region_source": self.region_source,
            "image_uri": self.image_uri,
            "required_apis": list(self.required_apis),
            "secrets": None if self.secrets is None else self.secrets.to_dict(),
            "production_posture": (
                None if self.production_posture is None else self.production_posture.to_dict()
            ),
        }


@dataclass(frozen=True, slots=True)
class GCPDoctorEvaluation:
    ok: bool
    checks: tuple[DiagnosticCheck, ...]
    details: GCPDoctorDetails


def evaluate_gcp_cloud_run_readiness(
    *,
    source_project_paths: ProjectPaths | None,
    full: bool,
    explicit_project: str | None,
    explicit_region: str | None,
    project_config: ProjectConfig | None = None,
) -> GCPDoctorEvaluation:
    adapters = GCPAdapters.create()
    checks: list[DiagnosticCheck] = []
    settings: Settings | None = None
    account: str | None = None
    project_id: str | None = None
    project_source: str | None = None
    region: str | None = None
    region_source: str | None = None
    image_uri: str | None = None
    required_apis: tuple[dict[str, object], ...] = ()
    secrets: GCPDoctorSecretReadiness | None = None
    production_posture: GCPDoctorProductionPosture | None = None

    env_exists = source_project_paths is not None and source_project_paths.env_file.is_file()
    if source_project_paths is None:
        checks.append(
            DiagnosticCheck(
                id="backend_env_exists",
                status="warn",
                message=(
                    "No source checkout detected in this workspace; skipping backend/.env "
                    "validation for managed Cloud Run readiness."
                ),
                action="Switch to runtime_source=source if you need local source-backed checks.",
            )
        )
    else:
        checks.append(
            DiagnosticCheck(
                id="backend_env_exists",
                status="pass" if env_exists else "fail",
                message=(
                    f"{source_project_paths.env_file} exists"
                    if env_exists
                    else "backend/.env is missing"
                ),
                action=None if env_exists else "Run 'portworld init' first",
            )
        )
    gcloud_result = adapters.auth.probe_gcloud()
    gcloud_available = gcloud_result.ok
    checks.append(
        DiagnosticCheck(
            id="gcloud_installed",
            status="pass" if gcloud_available else "fail",
            message=(
                "gcloud is installed"
                if gcloud_available
                else _error_message(gcloud_result, fallback="gcloud is not available")
            ),
            action=None if gcloud_available else _error_action(
                gcloud_result,
                fallback="Install the Google Cloud SDK and make `gcloud` available on PATH.",
            ),
        )
    )

    account_result = adapters.auth.get_active_account() if gcloud_available else None
    if account_result is None:
        checks.append(
            DiagnosticCheck(
                id="gcloud_authenticated",
                status="fail",
                message="An authenticated gcloud account could not be checked because gcloud is unavailable.",
                action="Install the Google Cloud SDK and run `gcloud auth login`.",
            )
        )
    elif not account_result.ok:
        checks.append(
            DiagnosticCheck(
                id="gcloud_authenticated",
                status="fail",
                message=_error_message(account_result, fallback="Unable to determine the active gcloud account."),
                action=_error_action(
                    account_result,
                    fallback="Run `gcloud auth login` and select the intended account.",
                ),
            )
        )
    elif account_result.value is None:
        checks.append(
            DiagnosticCheck(
                id="gcloud_authenticated",
                status="fail",
                message="No active gcloud account is configured.",
                action="Run `gcloud auth login` and select the intended account.",
            )
        )
    else:
        account = account_result.value.account
        checks.append(
            DiagnosticCheck(
                id="gcloud_authenticated",
                status="pass",
                message=f"Authenticated gcloud account: {account}",
            )
        )

    if explicit_project is not None and explicit_project.strip():
        configured_project = _ConfiguredValueResult(value=None)
    else:
        configured_project = _get_configured_project(
            adapters=adapters,
            gcloud_available=gcloud_available,
        )
        if configured_project.error_check is not None:
            checks.append(configured_project.error_check)
    resolved_project = resolve_project_id(
        explicit_project_id=explicit_project,
        project_config_project_id=(
            None
            if project_config is None
            else project_config.deploy.gcp_cloud_run.project_id
        ),
        configured_project_id=configured_project.value,
    )
    project_id = resolved_project.value
    project_source = None if project_id is None else resolved_project.source
    checks.append(
        DiagnosticCheck(
            id="gcp_project_selected",
            status="pass" if project_id is not None else "fail",
            message=(
                f"Using GCP project '{project_id}' from {resolved_project.source}"
                if project_id is not None
                else "No GCP project is selected for Cloud Run checks."
            ),
            action=(
                None
                if project_id is not None
                else "Pass --project <project-id> or run `gcloud config set project <project-id>` and rerun the doctor command."
            ),
        )
    )

    if explicit_region is not None and explicit_region.strip():
        configured_region = _ConfiguredValueResult(value=None)
    else:
        configured_region = _get_configured_region(
            adapters=adapters,
            gcloud_available=gcloud_available,
        )
        if configured_region.error_check is not None:
            checks.append(configured_region.error_check)
    resolved_region = resolve_region(
        explicit_region=explicit_region,
        project_config_region=(
            None
            if project_config is None
            else project_config.deploy.gcp_cloud_run.region
        ),
        configured_region=configured_region.value,
    )
    region = resolved_region.value
    region_source = None if region is None else resolved_region.source
    checks.append(
        DiagnosticCheck(
            id="gcp_region_selected",
            status="pass" if region is not None else "fail",
            message=(
                f"Using Cloud Run region '{region}' from {resolved_region.source}"
                if region is not None
                else "No Cloud Run region is selected for Cloud Run checks."
            ),
            action=(
                None
                if region is not None
                else (
                    "Pass --region <region>, for example --region "
                    f"{SUGGESTED_DEFAULT_REGION}, or run `gcloud config set run/region {SUGGESTED_DEFAULT_REGION}`."
                )
            ),
        )
    )

    if env_exists and source_project_paths is not None:
        try:
            settings = _build_settings(source_project_paths)
            checks.append(
                DiagnosticCheck(
                    id="settings_loaded",
                    status="pass",
                    message=f"Loaded backend settings from {source_project_paths.env_file}",
                )
            )
        except Exception as exc:
            checks.append(
                DiagnosticCheck(
                    id="settings_loaded",
                    status="fail",
                    message=str(exc),
                    action="Fix backend/.env so the CLI can parse the backend settings.",
                )
            )

    if settings is not None:
        checks.extend(_build_runtime_validation_checks(settings))
        secrets = _build_secret_readiness(settings)
        production_posture = _build_production_posture(settings)
        checks.extend(_build_secret_checks(settings=settings, secrets=secrets))
        checks.extend(_build_production_posture_checks(production_posture))
    elif source_project_paths is None:
        checks.append(
            DiagnosticCheck(
                id="settings_loaded",
                status="warn",
                message=(
                    "Skipping backend runtime validation because this workspace does not include "
                    "a source checkout."
                ),
                action="Run the command from a source checkout when you need backend/.env validation.",
            )
        )

    if project_id is not None and region is not None:
        runtime_source = (
            None if project_config is None else project_config.runtime_source
        )
        artifact_repository = (
            DEFAULT_ARTIFACT_REPOSITORY
            if project_config is None
            else project_config.deploy.gcp_cloud_run.artifact_repository
        )
        image_tag = DOCTOR_IMAGE_TAG
        image_source_mode = "source_build"
        if runtime_source == RUNTIME_SOURCE_PUBLISHED:
            artifact_repository = derive_published_artifact_repository(artifact_repository)
            image_tag = (
                project_config.deploy.published_runtime.release_tag
                or DOCTOR_IMAGE_TAG
            )
            image_source_mode = IMAGE_SOURCE_MODE_PUBLISHED_RELEASE
        image_uri = build_image_uri(
            project_id=project_id,
            region=region,
            repository=artifact_repository,
            image_name=IMAGE_NAME,
            tag=image_tag,
        )
        checks.append(
            DiagnosticCheck(
                id="deployable_image_path",
                status="pass",
                message=(
                    f"Deployable image path can be derived for {image_source_mode}: {image_uri}"
                ),
            )
        )
    else:
        checks.append(
            DiagnosticCheck(
                id="deployable_image_path",
                status="fail",
                message="Deployable image path cannot be derived until both project and region are resolved.",
                action="Resolve the GCP project and Cloud Run region, then rerun the doctor command.",
            )
        )

    if project_id is not None and gcloud_available:
        api_result = adapters.service_usage.get_api_statuses(
            project_id=project_id,
            service_names=REQUIRED_GCP_SERVICES,
        )
        if not api_result.ok:
            checks.append(
                DiagnosticCheck(
                    id="required_apis_ready",
                    status="fail",
                    message=_error_message(api_result, fallback="Unable to inspect required GCP APIs."),
                    action=_error_action(
                        api_result,
                        fallback="Use an account with permission to inspect Service Usage in the selected project.",
                    ),
                )
            )
        else:
            statuses = api_result.value or ()
            required_apis = tuple(
                {
                    "service_name": status.service_name,
                    "enabled": status.enabled,
                }
                for status in statuses
            )
            disabled = [status.service_name for status in statuses if not status.enabled]
            checks.append(
                DiagnosticCheck(
                    id="required_apis_ready",
                    status="pass" if not disabled else "warn",
                    message=(
                        "All required GCP APIs are enabled."
                        if not disabled
                        else "Required GCP APIs are disabled: " + ", ".join(disabled)
                    ),
                    action=(
                        None
                        if not disabled
                        else (
                            "Enable the missing APIs with "
                            f"`gcloud services enable <services> --project {project_id}`, "
                            "or let `portworld deploy gcp-cloud-run` enable them during deploy."
                        )
                    ),
                )
            )
    else:
        if not gcloud_available:
            checks.append(
                DiagnosticCheck(
                    id="required_apis_ready",
                    status="fail",
                    message="Required GCP APIs cannot be inspected because gcloud is unavailable.",
                    action="Install the Google Cloud SDK and rerun the doctor command.",
                )
            )
        else:
            checks.append(
                DiagnosticCheck(
                    id="required_apis_ready",
                    status="fail",
                    message="Required GCP APIs cannot be inspected until a GCP project is resolved.",
                    action="Resolve the GCP project and rerun the doctor command.",
                )
            )

    details = GCPDoctorDetails(
        account=account,
        project_id=project_id,
        project_source=project_source,
        region=region,
        region_source=region_source,
        image_uri=image_uri,
        required_apis=required_apis,
        secrets=secrets,
        production_posture=production_posture,
    )
    ok = not any(check.status == "fail" for check in checks)
    return GCPDoctorEvaluation(ok=ok, checks=tuple(checks), details=details)


@dataclass(frozen=True, slots=True)
class _ConfiguredValueResult:
    value: str | None
    error_check: DiagnosticCheck | None = None


def _get_configured_project(*, adapters: GCPAdapters, gcloud_available: bool) -> _ConfiguredValueResult:
    if not gcloud_available:
        return _ConfiguredValueResult(value=None)
    result = adapters.auth.get_configured_project()
    if result.ok:
        return _ConfiguredValueResult(value=result.value)
    return _ConfiguredValueResult(
        value=None,
        error_check=DiagnosticCheck(
            id="gcloud_project_config_available",
            status="warn",
            message=_error_message(result, fallback="Unable to read the configured gcloud project."),
            action=_error_action(
                result,
                fallback="Pass --project <project-id> if you do not want to use the local gcloud config.",
            ),
        ),
    )


def _get_configured_region(*, adapters: GCPAdapters, gcloud_available: bool) -> _ConfiguredValueResult:
    if not gcloud_available:
        return _ConfiguredValueResult(value=None)
    result = adapters.auth.get_configured_run_region()
    if result.ok:
        return _ConfiguredValueResult(value=result.value)
    return _ConfiguredValueResult(
        value=None,
        error_check=DiagnosticCheck(
            id="gcloud_region_config_available",
            status="warn",
            message=_error_message(result, fallback="Unable to read the configured gcloud run/region value."),
            action=_error_action(
                result,
                fallback=(
                    "Pass --region <region>, for example --region "
                    f"{SUGGESTED_DEFAULT_REGION}, if you do not want to use the local gcloud config."
                ),
            ),
        ),
    )


def _build_settings(paths: ProjectPaths) -> Settings:
    load_environment_files(paths.env_file)
    return Settings.from_env()


def _build_runtime_validation_checks(settings: Settings) -> tuple[DiagnosticCheck, ...]:
    checks: list[DiagnosticCheck] = []
    try:
        realtime_provider_factory = RealtimeProviderFactory(settings=settings)
        realtime_provider_factory.validate_configuration()
        if settings.vision_memory_enabled:
            vision_factory = VisionAnalyzerFactory(settings=settings)
            vision_factory.validate_configuration()
            vision_summary = f"; vision provider '{vision_factory.provider_name}' is configured"
        else:
            vision_summary = "; visual memory is disabled"
        if settings.realtime_tooling_enabled:
            search_provider_factory = SearchProviderFactory(settings=settings)
            tooling_summary = (
                f"; realtime tooling uses web search provider '{search_provider_factory.provider_name}'"
            )
        else:
            tooling_summary = "; realtime tooling is disabled"
        checks.append(
            DiagnosticCheck(
                id="backend_config_valid",
                status="pass",
                message=(
                    f"Backend config is valid for realtime provider '{realtime_provider_factory.provider_name}'"
                    f"{vision_summary}{tooling_summary}"
                ),
            )
        )
    except Exception as exc:
        checks.append(
            DiagnosticCheck(
                id="backend_config_valid",
                status="fail",
                message=str(exc),
                action="Fix the backend provider or feature settings in backend/.env.",
            )
        )
    return tuple(checks)


def _build_secret_readiness(settings: Settings) -> GCPDoctorSecretReadiness:
    vision_key_present: bool | None = None
    if settings.vision_memory_enabled:
        vision_key_present = bool((settings.vision_provider_api_key or settings.mistral_api_key or "").strip())

    tavily_key_present: bool | None = None
    if settings.realtime_tooling_enabled:
        tavily_key_present = bool((settings.tavily_api_key or "").strip())

    return GCPDoctorSecretReadiness(
        openai_api_key_present=bool((settings.openai_api_key or "").strip()),
        vision_provider_secret_required=settings.vision_memory_enabled,
        vision_provider_api_key_present=vision_key_present,
        tavily_secret_required=settings.realtime_tooling_enabled,
        tavily_api_key_present=tavily_key_present,
        backend_bearer_token_present=bool((settings.backend_bearer_token or "").strip()),
    )


def _build_production_posture(settings: Settings) -> GCPDoctorProductionPosture:
    return GCPDoctorProductionPosture(
        backend_profile=settings.backend_profile,
        profile_is_production=settings.is_production_profile,
        cors_origins_explicit=settings.cors_origins != ["*"],
        allowed_hosts_explicit=settings.backend_allowed_hosts != ["*"],
        debug_trace_disabled=not settings.backend_debug_trace_ws_messages,
    )


def _build_secret_checks(
    *,
    settings: Settings,
    secrets: GCPDoctorSecretReadiness,
) -> tuple[DiagnosticCheck, ...]:
    checks: list[DiagnosticCheck] = []
    checks.append(
        DiagnosticCheck(
            id="openai_secret_ready",
            status="pass" if secrets.openai_api_key_present else "fail",
            message=(
                "OPENAI_API_KEY is present locally and can be uploaded to Secret Manager."
                if secrets.openai_api_key_present
                else "OPENAI_API_KEY is missing from backend/.env."
            ),
            action=None if secrets.openai_api_key_present else "Set OPENAI_API_KEY in backend/.env and rerun the doctor command.",
        )
    )

    if not settings.vision_memory_enabled:
        checks.append(
            DiagnosticCheck(
                id="vision_secret_ready",
                status="pass",
                message="Visual memory is disabled; no vision secret is required.",
            )
        )
    else:
        try:
            settings.validate_vision_provider_credentials()
            checks.append(
                DiagnosticCheck(
                    id="vision_secret_ready",
                    status="pass",
                    message="Vision provider credentials are present locally and can be uploaded to Secret Manager.",
                )
            )
        except Exception as exc:
            checks.append(
                DiagnosticCheck(
                    id="vision_secret_ready",
                    status="fail",
                    message=str(exc),
                    action="Set a valid VISION_PROVIDER_API_KEY or MISTRAL_API_KEY in backend/.env and rerun the doctor command.",
                )
            )

    if not settings.realtime_tooling_enabled:
        checks.append(
            DiagnosticCheck(
                id="tooling_secret_ready",
                status="pass",
                message="Realtime tooling is disabled; no web-search secret is required.",
            )
        )
    else:
        try:
            search_provider_factory = SearchProviderFactory(settings=settings)
        except Exception as exc:
            checks.append(
                DiagnosticCheck(
                    id="tooling_secret_ready",
                    status="fail",
                    message=str(exc),
                    action="Fix REALTIME_WEB_SEARCH_PROVIDER in backend/.env and rerun the doctor command.",
                )
            )
            return tuple(checks)
        if search_provider_factory.is_enabled():
            checks.append(
                DiagnosticCheck(
                    id="tooling_secret_ready",
                    status="pass",
                    message=(
                        f"Realtime tooling credentials for provider '{search_provider_factory.provider_name}' are present locally."
                    ),
                )
            )
        else:
            checks.append(
                DiagnosticCheck(
                    id="tooling_secret_ready",
                    status="warn",
                    message=(
                        "Realtime tooling is enabled but the configured web-search provider does not have active local credentials."
                    ),
                    action="Set TAVILY_API_KEY in backend/.env or disable realtime tooling before deploy.",
                )
            )

    checks.append(
        DiagnosticCheck(
            id="bearer_token_ready",
            status="pass" if secrets.backend_bearer_token_present else "warn",
            message=(
                "BACKEND_BEARER_TOKEN is present locally."
                if secrets.backend_bearer_token_present
                else "BACKEND_BEARER_TOKEN is missing locally; deploy can generate one."
            ),
            action=None if secrets.backend_bearer_token_present else "Set BACKEND_BEARER_TOKEN in backend/.env or let deploy generate one.",
        )
    )
    return tuple(checks)


def _build_production_posture_checks(
    posture: GCPDoctorProductionPosture,
) -> tuple[DiagnosticCheck, ...]:
    checks: list[DiagnosticCheck] = []
    checks.append(
        DiagnosticCheck(
            id="production_profile_ready",
            status="pass" if posture.profile_is_production else "warn",
            message=(
                "BACKEND_PROFILE is already set to production."
                if posture.profile_is_production
                else f"BACKEND_PROFILE is '{posture.backend_profile}'; deploy will override it to production."
            ),
            action=None if posture.profile_is_production else "No local change is required; deploy will set BACKEND_PROFILE=production.",
        )
    )
    checks.append(
        DiagnosticCheck(
            id="cors_origins_ready",
            status="pass" if posture.cors_origins_explicit else "warn",
            message=(
                "CORS_ORIGINS is explicit for production use."
                if posture.cors_origins_explicit
                else "CORS_ORIGINS is '*' in backend/.env."
            ),
            action=None if posture.cors_origins_explicit else "Provide explicit production CORS origins during deploy.",
        )
    )
    checks.append(
        DiagnosticCheck(
            id="allowed_hosts_ready",
            status="pass" if posture.allowed_hosts_explicit else "warn",
            message=(
                "BACKEND_ALLOWED_HOSTS is explicit for production use."
                if posture.allowed_hosts_explicit
                else "BACKEND_ALLOWED_HOSTS is '*' in backend/.env."
            ),
            action=None if posture.allowed_hosts_explicit else "Provide explicit production allowed hosts during deploy.",
        )
    )
    checks.append(
        DiagnosticCheck(
            id="debug_trace_ready",
            status="pass" if posture.debug_trace_disabled else "warn",
            message=(
                "BACKEND_DEBUG_TRACE_WS_MESSAGES is disabled for production use."
                if posture.debug_trace_disabled
                else "BACKEND_DEBUG_TRACE_WS_MESSAGES is enabled in backend/.env."
            ),
            action=None if posture.debug_trace_disabled else "Set BACKEND_DEBUG_TRACE_WS_MESSAGES=false before production deploy.",
        )
    )
    return tuple(checks)


def _error_message(result: object, *, fallback: str) -> str:
    error = getattr(result, "error", None)
    if error is None:
        return fallback
    message = getattr(error, "message", None)
    if isinstance(message, str) and message.strip():
        return message
    return fallback


def _error_action(result: object, *, fallback: str) -> str:
    error = getattr(result, "error", None)
    if error is None:
        return fallback
    action = getattr(error, "action", None)
    if isinstance(action, str) and action.strip():
        return action
    return fallback
