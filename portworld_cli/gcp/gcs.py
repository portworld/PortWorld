from __future__ import annotations

from dataclasses import dataclass

from portworld_cli.gcp.executor import GCloudExecutor
from portworld_cli.gcp.types import GCPResult, MutationOutcome


@dataclass(frozen=True, slots=True)
class GCSBucketRef:
    project_id: str
    name: str
    location: str | None


class GCSAdapter:
    def __init__(self, executor: GCloudExecutor) -> None:
        self._executor = executor

    def get_bucket(self, *, project_id: str, bucket_name: str) -> GCPResult[GCSBucketRef | None]:
        result = self._executor.run_json(
            [
                "storage",
                "buckets",
                "describe",
                f"gs://{bucket_name}",
                f"--project={project_id}",
                "--format=json",
            ]
        )
        if not result.ok:
            error = result.error
            if error is not None and error.code == "not_found":
                return GCPResult.success(None)
            return GCPResult.failure(error)  # type: ignore[arg-type]
        payload = result.value
        if not isinstance(payload, dict):
            return GCPResult.success(None)
        location = str(payload.get("location", "")).strip() or None
        return GCPResult.success(GCSBucketRef(project_id=project_id, name=bucket_name, location=location))

    def create_bucket(
        self,
        *,
        project_id: str,
        bucket_name: str,
        location: str,
    ) -> GCPResult[MutationOutcome[GCSBucketRef]]:
        existing = self.get_bucket(project_id=project_id, bucket_name=bucket_name)
        if not existing.ok:
            return GCPResult.failure(existing.error)  # type: ignore[arg-type]
        if existing.value is not None:
            return GCPResult.success(MutationOutcome(action="existing", resource=existing.value))

        result = self._executor.run_text(
            [
                "storage",
                "buckets",
                "create",
                f"gs://{bucket_name}",
                f"--project={project_id}",
                f"--location={location}",
                "--uniform-bucket-level-access",
            ]
        )
        if not result.ok:
            return GCPResult.failure(result.error)  # type: ignore[arg-type]
        return GCPResult.success(
            MutationOutcome(
                action="created",
                resource=GCSBucketRef(project_id=project_id, name=bucket_name, location=location),
            )
        )
