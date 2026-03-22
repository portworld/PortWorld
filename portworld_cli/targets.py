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

# Phase 1 keeps the user-visible managed CLI target list unchanged.
EXPOSED_MANAGED_TARGETS: tuple[str, ...] = (TARGET_GCP_CLOUD_RUN,)

MANAGED_TARGETS_BY_PROVIDER: dict[str, tuple[str, ...]] = {
    CLOUD_PROVIDER_GCP: (TARGET_GCP_CLOUD_RUN,),
    CLOUD_PROVIDER_AWS: (TARGET_AWS_ECS_FARGATE,),
    CLOUD_PROVIDER_AZURE: (TARGET_AZURE_CONTAINER_APPS,),
}


@dataclass(frozen=True, slots=True)
class ManagedTargetStatePaths:
    cli_state_dir: Path

    def file_for_target(self, target: str) -> Path:
        normalized_target = target.strip().lower()
        if normalized_target not in MANAGED_TARGETS:
            raise ValueError(f"Unsupported managed target: {target!r}")
        return self.cli_state_dir / f"{normalized_target}.json"

    def status_payload(self, *, exposed_only: bool = True) -> dict[str, str]:
        targets = EXPOSED_MANAGED_TARGETS if exposed_only else MANAGED_TARGETS
        payload: dict[str, str] = {}
        for target in targets:
            payload[target] = str(self.file_for_target(target))
        return payload
