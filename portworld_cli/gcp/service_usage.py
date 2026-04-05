from __future__ import annotations

from dataclasses import dataclass

from portworld_cli.gcp.executor import GCloudExecutor
from portworld_cli.gcp.types import GCPResult, MutationOutcome


@dataclass(frozen=True, slots=True)
class APIStatus:
    service_name: str
    enabled: bool


class ServiceUsageAdapter:
    def __init__(self, executor: GCloudExecutor) -> None:
        self._executor = executor

    def get_api_statuses(
        self,
        *,
        project_id: str,
        service_names: tuple[str, ...] | list[str],
    ) -> GCPResult[tuple[APIStatus, ...]]:
        result = self._executor.run_json(
            [
                "services",
                "list",
                "--enabled",
                f"--project={project_id}",
                "--format=json",
            ]
        )
        if not result.ok:
            return GCPResult.failure(result.error)  # type: ignore[arg-type]

        payload = result.value or []
        enabled_services: set[str] = set()
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    name = str(item.get("config", {}).get("name", "")).strip()
                    if name:
                        enabled_services.add(name)

        statuses = tuple(
            APIStatus(service_name=service_name, enabled=service_name in enabled_services)
            for service_name in service_names
        )
        return GCPResult.success(statuses)

    def enable_apis(
        self,
        *,
        project_id: str,
        service_names: tuple[str, ...] | list[str],
    ) -> GCPResult[MutationOutcome[tuple[APIStatus, ...]]]:
        names = [service_name for service_name in service_names if service_name]
        result = self._executor.run_text(
            [
                "services",
                "enable",
                *names,
                f"--project={project_id}",
            ],
            timeout_seconds=self._executor.long_timeout_seconds,
        )
        if not result.ok:
            return GCPResult.failure(result.error)  # type: ignore[arg-type]
        statuses = tuple(APIStatus(service_name=name, enabled=True) for name in names)
        return GCPResult.success(MutationOutcome(action="enabled", resource=statuses))
