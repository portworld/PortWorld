from __future__ import annotations

from collections import OrderedDict

from portworld_cli.output import CommandResult, DiagnosticCheck, format_key_value_lines
from portworld_cli.deploy.config import ResolvedDeployConfig


COMMAND_NAME = "portworld deploy gcp-cloud-run"

_DEPLOY_STAGE_LABELS: dict[str, str] = {
    "repo_config_discovery": "Loading workspace configuration",
    "prerequisite_validation": "Checking cloud credentials",
    "parameter_resolution": "Resolving deploy parameters",
    "api_enablement": "Enabling required GCP APIs",
    "service_account_setup": "Preparing runtime service account",
    "artifact_registry_setup": "Preparing container registry",
    "cloud_build": "Building and publishing backend image",
    "published_image_resolution": "Resolving published backend image",
    "secret_manager_setup": "Provisioning runtime secrets",
    "cloud_sql_setup": "Preparing managed database",
    "gcs_bucket_setup": "Preparing managed object storage",
    "runtime_config_assembly": "Assembling runtime configuration",
    "cloud_run_deploy": "Deploying Cloud Run service",
    "post_deploy_validation": "Validating deployed service",
    "state_write": "Writing deploy state",
    "aws_artifact_setup": "Preparing object storage",
    "aws_image_publish": "Building and publishing backend image",
    "aws_database_setup": "Preparing managed database",
    "aws_network_edge_setup": "Preparing network and edge routing",
    "aws_runtime_setup": "Deploying ECS service",
    "aws_rollout_wait": "Waiting for service rollout",
    "azure_subscription_set": "Selecting Azure subscription",
    "azure_platform_setup": "Preparing Azure platform resources",
    "azure_registry_setup": "Preparing container registry",
    "azure_runtime_infra": "Preparing runtime infrastructure",
    "azure_container_app_deploy": "Deploying Container App",
    "azure_rollout_wait": "Waiting for service rollout",
}


def record_stage(
    stage_records: list[dict[str, object]],
    *,
    stage: str,
    message: str,
    details: dict[str, object] | None = None,
) -> None:
    payload: dict[str, object] = {"stage": stage, "status": "ok", "message": message}
    if details:
        payload["details"] = details
    stage_records.append(payload)


def humanize_stage_label(stage: str) -> str:
    normalized = stage.strip()
    if not normalized:
        return "Working"
    label = _DEPLOY_STAGE_LABELS.get(normalized)
    if label is not None:
        return label
    words = normalized.replace("-", "_").split("_")
    return " ".join(word.capitalize() for word in words if word) or "Working"


def build_feature_summary(env_values: OrderedDict[str, str]) -> dict[str, object]:
    tooling_enabled = _parse_bool_string(env_values.get("REALTIME_TOOLING_ENABLED", "false"))
    return {
        "vision_memory": _parse_bool_string(env_values.get("VISION_MEMORY_ENABLED", "false")),
        "realtime_tooling": tooling_enabled,
        "web_search_provider": (env_values.get("REALTIME_WEB_SEARCH_PROVIDER") if tooling_enabled else None),
    }


def build_next_steps(
    *,
    service_url: str | None,
    project_id: str,
    region: str,
    bearer_secret_name: str,
) -> list[str]:
    if service_url is None:
        return [f"Run `portworld doctor --target gcp-cloud-run --gcp-project {project_id} --gcp-region {region}`"]
    base_url = service_url.rstrip("/")
    return [
        f"curl {base_url}/livez",
        (
            "curl -H \"Authorization: Bearer $(gcloud secrets versions access latest "
            f"--secret={bearer_secret_name} --project={project_id})\" {base_url}/readyz"
        ),
        f"Run `portworld doctor --target gcp-cloud-run --gcp-project {project_id} --gcp-region {region}`",
    ]


def build_success_message(
    *,
    config: ResolvedDeployConfig,
    service_url: str | None,
    image_uri: str,
    service_account_email: str,
    bucket_name: str,
    features: dict[str, object],
    next_steps: list[str],
) -> str:
    lines = [
        format_key_value_lines(
            ("project_id", config.project_id),
            ("region", config.region),
            ("service_name", config.service_name),
            ("service_url", service_url),
            ("runtime_source", config.runtime_source),
            ("image_source_mode", config.image_source_mode),
            ("published_release_tag", config.published_release_tag),
            ("published_image_ref", config.published_image_ref),
            ("image", image_uri),
            ("artifact_repository", config.artifact_repository),
            ("cloud_sql_instance", config.sql_instance_name),
            ("database_name", config.database_name),
            ("cloud_sql_role", "operational_metadata"),
            ("bucket_name", bucket_name),
            ("memory_source_of_truth", "object_store_files"),
            ("service_account", service_account_email),
            ("vision_memory", features.get("vision_memory")),
            ("realtime_tooling", features.get("realtime_tooling")),
            ("web_search_provider", features.get("web_search_provider")),
        )
    ]
    lines.append("next_steps:")
    for step in next_steps:
        lines.append(f"- {step}")
    return "\n".join(line for line in lines if line)


def build_failure_result(
    *,
    stage: str,
    exc: Exception,
    stage_records: list[dict[str, object]],
    resources: dict[str, object],
    action: str | None,
    error_type: str,
    exit_code: int = 1,
) -> CommandResult:
    message = str(exc)
    next_step = action or "Inspect the stage details in output and rerun deploy."
    checks = ()
    if next_step:
        checks = (
            DiagnosticCheck(
                id="next-step",
                status="warn",
                message=message,
                action=next_step,
            ),
        )
    return CommandResult(
        ok=False,
        command=COMMAND_NAME,
        message=_problem_next_message(
            stage=stage,
            problem=message,
            next_step=next_step,
        ),
        data={
            "stage": stage,
            "error_type": error_type,
            "stages": stage_records,
            "resources": resources,
        },
        checks=checks,
        exit_code=exit_code,
    )


def _parse_bool_string(raw_value: str) -> bool:
    return raw_value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _problem_next_message(*, problem: str, next_step: str, stage: str | None = None) -> str:
    lines: list[str] = []
    if stage:
        lines.append(f"stage: {stage}")
    lines.append(f"problem: {problem}")
    lines.append(f"next: {next_step}")
    return "\n".join(lines)
