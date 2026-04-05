from __future__ import annotations

from dataclasses import dataclass

from portworld_cli.gcp.executor import GCloudExecutor
from portworld_cli.gcp.types import GCPResult, MutationOutcome


@dataclass(frozen=True, slots=True)
class SecretRef:
    project_id: str
    name: str


@dataclass(frozen=True, slots=True)
class SecretVersionRef:
    project_id: str
    secret_name: str
    version: str | None


class SecretManagerAdapter:
    def __init__(self, executor: GCloudExecutor) -> None:
        self._executor = executor

    def get_secret(self, *, project_id: str, secret_name: str) -> GCPResult[SecretRef | None]:
        result = self._executor.run_json(
            [
                "secrets",
                "describe",
                secret_name,
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
        return GCPResult.success(SecretRef(project_id=project_id, name=secret_name))

    def create_secret(self, *, project_id: str, secret_name: str) -> GCPResult[MutationOutcome[SecretRef]]:
        existing = self.get_secret(project_id=project_id, secret_name=secret_name)
        if not existing.ok:
            return GCPResult.failure(existing.error)  # type: ignore[arg-type]
        if existing.value is not None:
            return GCPResult.success(MutationOutcome(action="existing", resource=existing.value))

        result = self._executor.run_text(
            [
                "secrets",
                "create",
                secret_name,
                f"--project={project_id}",
                "--replication-policy=automatic",
            ]
        )
        if not result.ok:
            return GCPResult.failure(result.error)  # type: ignore[arg-type]
        return GCPResult.success(
            MutationOutcome(
                action="created",
                resource=SecretRef(project_id=project_id, name=secret_name),
            )
        )

    def add_secret_version(
        self,
        *,
        project_id: str,
        secret_name: str,
        secret_value: str,
    ) -> GCPResult[MutationOutcome[SecretVersionRef]]:
        display_args = [
            "secrets",
            "versions",
            "add",
            secret_name,
            f"--project={project_id}",
            "--data-file=-",
        ]
        result = self._executor.run_json(
            [
                "secrets",
                "versions",
                "add",
                secret_name,
                f"--project={project_id}",
                "--data-file=-",
                "--format=json",
            ],
            input_text=secret_value,
            display_args=display_args,
        )
        if not result.ok:
            return GCPResult.failure(result.error)  # type: ignore[arg-type]
        payload = result.value
        version = None
        if isinstance(payload, dict):
            name = str(payload.get("name", "")).strip()
            if name:
                version = name.rsplit("/", 1)[-1]
        return GCPResult.success(
            MutationOutcome(
                action="updated",
                resource=SecretVersionRef(
                    project_id=project_id,
                    secret_name=secret_name,
                    version=version,
                ),
            )
        )
