from __future__ import annotations

from dataclasses import dataclass

from backend.cli_app.gcp.artifact_registry import ArtifactRegistryAdapter
from backend.cli_app.gcp.auth import AuthAdapter
from backend.cli_app.gcp.cloud_build import CloudBuildAdapter
from backend.cli_app.gcp.cloud_run import CloudRunAdapter
from backend.cli_app.gcp.cloud_sql import CloudSQLAdapter
from backend.cli_app.gcp.executor import GCloudExecutor
from backend.cli_app.gcp.gcs import GCSAdapter
from backend.cli_app.gcp.iam import IAMAdapter
from backend.cli_app.gcp.logging import GCPLoggingAdapter
from backend.cli_app.gcp.secret_manager import SecretManagerAdapter
from backend.cli_app.gcp.service_usage import ServiceUsageAdapter


@dataclass(frozen=True, slots=True)
class GCPAdapters:
    executor: GCloudExecutor
    auth: AuthAdapter
    service_usage: ServiceUsageAdapter
    iam: IAMAdapter
    artifact_registry: ArtifactRegistryAdapter
    cloud_build: CloudBuildAdapter
    cloud_run: CloudRunAdapter
    logging: GCPLoggingAdapter
    secret_manager: SecretManagerAdapter
    cloud_sql: CloudSQLAdapter
    gcs: GCSAdapter

    @classmethod
    def create(cls, *, executor: GCloudExecutor | None = None) -> "GCPAdapters":
        resolved_executor = executor or GCloudExecutor()
        return cls(
            executor=resolved_executor,
            auth=AuthAdapter(resolved_executor),
            service_usage=ServiceUsageAdapter(resolved_executor),
            iam=IAMAdapter(resolved_executor),
            artifact_registry=ArtifactRegistryAdapter(resolved_executor),
            cloud_build=CloudBuildAdapter(resolved_executor),
            cloud_run=CloudRunAdapter(resolved_executor),
            logging=GCPLoggingAdapter(resolved_executor),
            secret_manager=SecretManagerAdapter(resolved_executor),
            cloud_sql=CloudSQLAdapter(resolved_executor),
            gcs=GCSAdapter(resolved_executor),
        )
