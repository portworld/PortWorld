from __future__ import annotations

from dataclasses import dataclass

from dotenv import dotenv_values

from portworld_cli.deploy_artifacts import (
    IMAGE_NAME,
    IMAGE_SOURCE_MODE_PUBLISHED_RELEASE,
    derive_published_artifact_repository,
    derive_remote_image_name,
)
from portworld_cli.gcp import (
    GCPAdapters,
    REQUIRED_GCP_SERVICES,
    build_image_uri,
    resolve_project_id,
    resolve_region,
)
from portworld_cli.output import DiagnosticCheck
from portworld_cli.workspace.discovery.paths import ProjectPaths
from portworld_cli.workspace.project_config import (
    DEFAULT_GCP_ARTIFACT_REPOSITORY,
    DEFAULT_GCP_REGION,
    RUNTIME_SOURCE_PUBLISHED,
    ProjectConfig,
)
from portworld_cli.runtime.source_backend import (
    coerce_source_backend_payload,
    run_source_backend_cli,
)
from portworld_shared.backend_env import build_backend_env_contract
from portworld_shared.providers import build_provider_requirement_diagnostics


DEFAULT_ARTIFACT_REPOSITORY = DEFAULT_GCP_ARTIFACT_REPOSITORY
DOCTOR_IMAGE_TAG = "doctor-check"
SUGGESTED_DEFAULT_REGION = DEFAULT_GCP_REGION


@dataclass(frozen=True, slots=True)
class GCPDoctorSecretReadiness:
    selected_realtime_provider: str
    selected_vision_provider: str | None
    selected_search_provider: str | None
    required_secret_keys: tuple[str, ...]
    missing_required_secret_keys: tuple[str, ...]
    required_non_secret_config_keys: tuple[str, ...]
    missing_required_non_secret_config_keys: tuple[str, ...]
    key_presence: dict[str, bool]
    non_secret_config_key_presence: dict[str, bool]
    backend_bearer_token_present: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "selected_realtime_provider": self.selected_realtime_provider,
            "selected_vision_provider": self.selected_vision_provider,
            "selected_search_provider": self.selected_search_provider,
            "required_secret_keys": list(self.required_secret_keys),
            "missing_required_secret_keys": list(self.missing_required_secret_keys),
            "required_non_secret_config_keys": list(self.required_non_secret_config_keys),
            "missing_required_non_secret_config_keys": list(
                self.missing_required_non_secret_config_keys
            ),
            "key_presence": dict(self.key_presence),
            "non_secret_config_key_presence": dict(self.non_secret_config_key_presence),
            "backend_bearer_token_present": self.backend_bearer_token_present,
        }


@dataclass(frozen=True, slots=True)
class GCPDoctorProductionPosture:
    backend_profile: str
    profile_is_production: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "backend_profile": self.backend_profile,
            "profile_is_production": self.profile_is_production,
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
    env_values: dict[str, str] | None = None
    backend_contract = None
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
        env_values = _load_env_values(source_project_paths)
        backend_contract = build_backend_env_contract(env_values)
        checks.append(
            DiagnosticCheck(
                id="settings_loaded",
                status="pass",
                message=f"Using backend settings from {source_project_paths.env_file}",
            )
        )
        runtime_payload = _collect_runtime_validation_payload(
            source_project_paths=source_project_paths,
            full=full,
        )
        if runtime_payload is None:
            checks.append(
                DiagnosticCheck(
                    id="backend_config_valid",
                    status="fail",
                    message="Backend config validation did not return a result.",
                    action="Fix the backend profile or provider settings in backend/.env.",
                )
            )
        elif runtime_payload.get("status") == "error":
            checks.append(
                DiagnosticCheck(
                    id="backend_config_valid",
                    status="fail",
                    message=str(runtime_payload.get("message") or "Backend config validation failed."),
                    action="Fix the backend profile or provider settings in backend/.env.",
                )
            )
        else:
            checks.extend(_build_runtime_validation_checks(runtime_payload))
        secrets = _build_secret_readiness(env_values)
        production_posture = _build_production_posture(backend_contract)
        checks.extend(_build_secret_checks(secrets=secrets))
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
        image_name = IMAGE_NAME
        if runtime_source == RUNTIME_SOURCE_PUBLISHED:
            artifact_repository = derive_published_artifact_repository(artifact_repository)
            image_tag = (
                project_config.deploy.published_runtime.release_tag
                or DOCTOR_IMAGE_TAG
            )
            image_source_mode = IMAGE_SOURCE_MODE_PUBLISHED_RELEASE
            image_name = derive_remote_image_name(
                project_config.deploy.published_runtime.image_ref or "",
                fallback_image_name=IMAGE_NAME,
            )
        image_uri = build_image_uri(
            project_id=project_id,
            region=region,
            repository=artifact_repository,
            image_name=image_name,
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


def _load_env_values(paths: ProjectPaths) -> dict[str, str]:
    return {
        key: "" if value is None else str(value)
        for key, value in dotenv_values(paths.env_file).items()
        if key is not None
    }


def _collect_runtime_validation_payload(
    *,
    source_project_paths: ProjectPaths,
    full: bool,
) -> dict[str, object] | None:
    backend_args = ["check-config"]
    if full:
        backend_args.append("--full-readiness")
    completed = run_source_backend_cli(
        source_project_paths,
        backend_args=backend_args,
    )
    payload = coerce_source_backend_payload(
        completed,
        default_message="Backend config validation did not return structured JSON output.",
    )
    return payload


def _build_runtime_validation_checks(payload: dict[str, object]) -> tuple[DiagnosticCheck, ...]:
    checks: list[DiagnosticCheck] = []
    realtime_provider = _coerce_text(payload.get("realtime_provider")) or "unknown"
    vision_provider = _coerce_text(payload.get("vision_provider"))
    realtime_tooling_enabled = bool(payload.get("realtime_tooling_enabled"))
    web_search_provider = _coerce_text(payload.get("web_search_provider"))
    vision_summary = (
        f"; vision provider '{vision_provider}' is configured"
        if vision_provider is not None
        else "; visual memory is disabled"
    )
    tooling_summary = (
        f"; realtime tooling uses web search provider '{web_search_provider}'"
        if realtime_tooling_enabled and web_search_provider is not None
        else "; realtime tooling is enabled"
        if realtime_tooling_enabled
        else "; realtime tooling is disabled"
    )
    checks.append(
        DiagnosticCheck(
            id="backend_config_valid",
            status="pass",
            message=(
                f"Backend config is valid for realtime provider '{realtime_provider}'"
                f"{vision_summary}{tooling_summary}"
            ),
        )
    )
    checks.extend(_build_managed_storage_architecture_checks(payload))
    return tuple(checks)


def _build_managed_storage_architecture_checks(payload: dict[str, object]) -> tuple[DiagnosticCheck, ...]:
    checks: list[DiagnosticCheck] = []
    backend_storage_backend = _coerce_text(payload.get("storage_backend")) or "local"
    storage_details = payload.get("storage_details")
    object_store_provider = None
    if isinstance(storage_details, dict):
        object_store_provider = _coerce_text(storage_details.get("object_store_provider"))
    checks.append(
        DiagnosticCheck(
            id="managed_memory_architecture",
            status="pass",
            message=(
                "Recommended managed GCP architecture uses object-store files as memory source of truth; "
                "Cloud SQL is reserved for operational runtime metadata."
            ),
        )
    )
    checks.append(
        DiagnosticCheck(
            id="managed_storage_backend_shape",
            status="pass" if backend_storage_backend == "managed" else "warn",
            message=(
                "BACKEND_STORAGE_BACKEND is already managed."
                if backend_storage_backend == "managed"
                else (
                    f"BACKEND_STORAGE_BACKEND is '{backend_storage_backend}'; "
                    "deploy will override it to managed."
                )
            ),
            action=(
                None
                if backend_storage_backend == "managed"
                else "No local change is required; deploy will set BACKEND_STORAGE_BACKEND=managed."
            ),
        )
    )
    checks.append(
        DiagnosticCheck(
            id="managed_object_store_provider_shape",
            status="pass" if object_store_provider == "gcs" else "warn",
            message=(
                "BACKEND_OBJECT_STORE_PROVIDER is already gcs."
                if object_store_provider == "gcs"
                else (
                    f"BACKEND_OBJECT_STORE_PROVIDER is '{object_store_provider or 'filesystem'}'; "
                    "deploy will override it to gcs."
                )
            ),
            action=(
                None
                if object_store_provider == "gcs"
                else "No local change is required; deploy will set BACKEND_OBJECT_STORE_PROVIDER=gcs."
            ),
        )
    )
    return tuple(checks)


def _build_secret_readiness(env_values: dict[str, str]) -> GCPDoctorSecretReadiness:
    diagnostics = build_provider_requirement_diagnostics(env_values)
    return GCPDoctorSecretReadiness(
        selected_realtime_provider=diagnostics.selected.realtime_provider,
        selected_vision_provider=diagnostics.selected.vision_provider,
        selected_search_provider=diagnostics.selected.search_provider,
        required_secret_keys=diagnostics.required_secret_env_keys,
        missing_required_secret_keys=diagnostics.missing_required_secret_env_keys,
        required_non_secret_config_keys=diagnostics.required_non_secret_env_keys,
        missing_required_non_secret_config_keys=diagnostics.missing_required_non_secret_env_keys,
        key_presence={
            key: diagnostics.secret_key_presence.get(key, False)
            for key in diagnostics.required_secret_env_keys
        },
        non_secret_config_key_presence={
            key: diagnostics.non_secret_key_presence.get(key, False)
            for key in diagnostics.required_non_secret_env_keys
        },
        backend_bearer_token_present=bool((env_values.get("BACKEND_BEARER_TOKEN", "") or "").strip()),
    )


def _build_production_posture(backend_contract) -> GCPDoctorProductionPosture:
    return GCPDoctorProductionPosture(
        backend_profile=backend_contract.backend_profile,
        profile_is_production=backend_contract.is_production_profile,
    )


def _build_secret_checks(
    *,
    secrets: GCPDoctorSecretReadiness,
) -> tuple[DiagnosticCheck, ...]:
    checks: list[DiagnosticCheck] = []
    if not secrets.required_secret_keys and not secrets.required_non_secret_config_keys:
        checks.append(
            DiagnosticCheck(
                id="provider_secrets_ready",
                status="pass",
                message="Selected providers do not require additional managed secrets or non-secret provider config.",
            )
        )
    else:
        for key in secrets.required_secret_keys:
            present = secrets.key_presence.get(key, False)
            checks.append(
                DiagnosticCheck(
                    id=f"provider_secret_{key.lower()}",
                    status="pass" if present else "fail",
                    message=(
                        f"{key} is present locally and can be uploaded to Secret Manager."
                        if present
                        else f"{key} is missing from backend/.env."
                    ),
                    action=(
                        None
                        if present
                        else f"Set {key} in backend/.env for the selected provider configuration and rerun the doctor command."
                    ),
                )
            )
        for key in secrets.required_non_secret_config_keys:
            present = secrets.non_secret_config_key_presence.get(key, False)
            checks.append(
                DiagnosticCheck(
                    id=f"provider_config_{key.lower()}",
                    status="pass" if present else "fail",
                    message=(
                        f"{key} is present locally for selected provider configuration."
                        if present
                        else f"{key} is missing from backend/.env."
                    ),
                    action=(
                        None
                        if present
                        else (
                            f"Set {key} in backend/.env for the selected provider configuration "
                            "and rerun the doctor command."
                        )
                    ),
                )
            )

    if secrets.selected_vision_provider is not None:
        checks.append(
            DiagnosticCheck(
                id="vision_provider_credentials_ready",
                status="pass"
                if not any(
                    key.startswith("VISION_")
                    for key in (
                        *secrets.missing_required_secret_keys,
                        *secrets.missing_required_non_secret_config_keys,
                    )
                )
                else "fail",
                message=(
                    f"Vision provider '{secrets.selected_vision_provider}' credential shape validation passed."
                    if not any(
                        key.startswith("VISION_")
                        for key in (
                            *secrets.missing_required_secret_keys,
                            *secrets.missing_required_non_secret_config_keys,
                        )
                    )
                    else (
                        f"Vision provider '{secrets.selected_vision_provider}' is missing required configuration."
                    )
                ),
                action=(
                    None
                    if not any(
                        key.startswith("VISION_")
                        for key in (
                            *secrets.missing_required_secret_keys,
                            *secrets.missing_required_non_secret_config_keys,
                        )
                    )
                    else "Fix vision-provider credentials in backend/.env for the selected provider and rerun the doctor command."
                ),
            )
        )

    if secrets.selected_search_provider is not None:
        missing_search_keys = [
            key
            for key in (
                *secrets.missing_required_secret_keys,
                *secrets.missing_required_non_secret_config_keys,
            )
            if key.startswith("TAVILY_")
        ]
        checks.append(
            DiagnosticCheck(
                id="tooling_provider_selected",
                status="pass" if not missing_search_keys else "fail",
                message=(
                    f"Realtime tooling uses search provider '{secrets.selected_search_provider}'."
                    if not missing_search_keys
                    else (
                        f"Realtime tooling search provider '{secrets.selected_search_provider}' is missing required configuration."
                    )
                ),
                action=(
                    None
                    if not missing_search_keys
                    else "Fix REALTIME_WEB_SEARCH_PROVIDER configuration in backend/.env and rerun the doctor command."
                ),
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
    return (
        DiagnosticCheck(
            id="production_profile_ready",
            status="pass" if posture.profile_is_production else "warn",
            message=(
                "BACKEND_PROFILE is already set to production."
                if posture.profile_is_production
                else f"BACKEND_PROFILE is '{posture.backend_profile}'; deploy will override it to production."
            ),
            action=None if posture.profile_is_production else "No local change is required; deploy will set BACKEND_PROFILE=production.",
        ),
    )


def _coerce_text(value: object) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


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
