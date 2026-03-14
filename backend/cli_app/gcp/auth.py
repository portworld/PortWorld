from __future__ import annotations

from dataclasses import dataclass

from backend.cli_app.gcp.executor import GCloudExecutor
from backend.cli_app.gcp.types import GCPResult, ResolvedValue


@dataclass(frozen=True, slots=True)
class GCloudInstallation:
    version_text: str


@dataclass(frozen=True, slots=True)
class GCloudAccount:
    account: str


class AuthAdapter:
    def __init__(self, executor: GCloudExecutor) -> None:
        self._executor = executor

    def probe_gcloud(self) -> GCPResult[GCloudInstallation]:
        result = self._executor.run_text(["version"])
        if not result.ok:
            return GCPResult.failure(result.error)  # type: ignore[arg-type]
        output = result.value
        assert output is not None
        return GCPResult.success(GCloudInstallation(version_text=output.stdout.strip()))

    def get_active_account(self) -> GCPResult[GCloudAccount | None]:
        result = self._executor.run_json(
            [
                "auth",
                "list",
                "--filter=status:ACTIVE",
                "--format=json",
            ]
        )
        if not result.ok:
            return GCPResult.failure(result.error)  # type: ignore[arg-type]
        payload = result.value
        if not payload:
            return GCPResult.success(None)
        if not isinstance(payload, list):
            return GCPResult.success(None)
        first = payload[0] if payload else None
        if not isinstance(first, dict):
            return GCPResult.success(None)
        account = str(first.get("account", "")).strip()
        if not account:
            return GCPResult.success(None)
        return GCPResult.success(GCloudAccount(account=account))

    def get_configured_project(self) -> GCPResult[str | None]:
        return self._get_config_value("project")

    def get_configured_run_region(self) -> GCPResult[str | None]:
        return self._get_config_value("run/region")

    def _get_config_value(self, key: str) -> GCPResult[str | None]:
        result = self._executor.run_text(["config", "get-value", key])
        if not result.ok:
            error = result.error
            if error is not None and error.code == "command_failed":
                return GCPResult.success(None)
            return GCPResult.failure(error)  # type: ignore[arg-type]
        output = result.value
        assert output is not None
        value = output.stdout.strip()
        if not value or value == "(unset)":
            return GCPResult.success(None)
        return GCPResult.success(value)


def resolve_project_id(
    *,
    explicit_project_id: str | None,
    configured_project_id: str | None,
    remembered_project_id: str | None = None,
    allow_remembered: bool = False,
) -> ResolvedValue[str]:
    if explicit_project_id and explicit_project_id.strip():
        return ResolvedValue(value=explicit_project_id.strip(), source="explicit")
    if configured_project_id and configured_project_id.strip():
        return ResolvedValue(value=configured_project_id.strip(), source="gcloud_config")
    if allow_remembered and remembered_project_id and remembered_project_id.strip():
        return ResolvedValue(value=remembered_project_id.strip(), source="remembered_state")
    return ResolvedValue(value=None, source="missing")


def resolve_region(
    *,
    explicit_region: str | None,
    configured_region: str | None,
    remembered_region: str | None = None,
    allow_remembered: bool = False,
    default_region: str | None = None,
) -> ResolvedValue[str]:
    if explicit_region and explicit_region.strip():
        return ResolvedValue(value=explicit_region.strip(), source="explicit")
    if configured_region and configured_region.strip():
        return ResolvedValue(value=configured_region.strip(), source="gcloud_config")
    if allow_remembered and remembered_region and remembered_region.strip():
        return ResolvedValue(value=remembered_region.strip(), source="remembered_state")
    if default_region and default_region.strip():
        return ResolvedValue(value=default_region.strip(), source="default")
    return ResolvedValue(value=None, source="missing")
