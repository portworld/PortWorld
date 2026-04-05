from __future__ import annotations

from dataclasses import dataclass

from portworld_cli.gcp.artifact_registry import ArtifactRegistryAdapter
from portworld_cli.gcp.auth import AuthAdapter
from portworld_cli.gcp.cloud_build import CloudBuildAdapter
from portworld_cli.gcp.cloud_run import CloudRunAdapter
from portworld_cli.gcp.cloud_sql import CloudSQLAdapter
from portworld_cli.gcp.executor import GCloudExecutor
from portworld_cli.gcp.gcs import GCSAdapter
from portworld_cli.gcp.iam import IAMAdapter
from portworld_cli.gcp.logging import GCPLoggingAdapter
from portworld_cli.gcp.secret_manager import SecretManagerAdapter
from portworld_cli.gcp.service_usage import ServiceUsageAdapter


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
