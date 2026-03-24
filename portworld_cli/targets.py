from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


TARGET_GCP_CLOUD_RUN = "gcp-cloud-run"
TARGET_AWS_ECS_FARGATE = "aws-ecs-fargate"
TARGET_AZURE_CONTAINER_APPS = "azure-container-apps"

CLOUD_PROVIDER_GCP = "gcp"
CLOUD_PROVIDER_AWS = "aws"
CLOUD_PROVIDER_AZURE = "azure"

MANAGED_TARGETS: tuple[str, ...] = (
    TARGET_GCP_CLOUD_RUN,
    TARGET_AWS_ECS_FARGATE,
    TARGET_AZURE_CONTAINER_APPS,
)

EXPOSED_MANAGED_TARGETS: tuple[str, ...] = (
    TARGET_GCP_CLOUD_RUN,
    TARGET_AWS_ECS_FARGATE,
    TARGET_AZURE_CONTAINER_APPS,
)

STATUS_STATE_PATH_KEYS: dict[str, str] = {
    TARGET_GCP_CLOUD_RUN: "gcp_cloud_run",
    TARGET_AWS_ECS_FARGATE: "aws_ecs_fargate",
    TARGET_AZURE_CONTAINER_APPS: "azure_container_apps",
}

MANAGED_TARGETS_BY_PROVIDER: dict[str, tuple[str, ...]] = {
    CLOUD_PROVIDER_GCP: (TARGET_GCP_CLOUD_RUN,),
    CLOUD_PROVIDER_AWS: (TARGET_AWS_ECS_FARGATE,),
    CLOUD_PROVIDER_AZURE: (TARGET_AZURE_CONTAINER_APPS,),
}

def normalize_managed_target(target: str | None) -> str | None:
    if target is None:
        return None
    normalized = target.strip().lower()
    if not normalized:
        return None
    if normalized in MANAGED_TARGETS:
        return normalized
    return None


@dataclass(frozen=True, slots=True)
class ManagedTargetStatePaths:
    cli_state_dir: Path

    def file_for_target(self, target: str) -> Path:
        normalized_target = normalize_managed_target(target)
        if normalized_target is None:
            raise ValueError(f"Unsupported managed target: {target!r}")
        return self.cli_state_dir / f"{normalized_target}.json"

    def status_payload(self, *, exposed_only: bool = True) -> dict[str, str]:
        targets = EXPOSED_MANAGED_TARGETS if exposed_only else MANAGED_TARGETS
        payload: dict[str, str] = {}
        for target in targets:
            key = STATUS_STATE_PATH_KEYS[target]
            payload[key] = str(self.file_for_target(target))
        return payload
