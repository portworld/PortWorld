from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.cli_app.state import read_json_state, write_json_state


@dataclass(frozen=True, slots=True)
class DeployState:
    project_id: str | None
    region: str | None
    service_name: str | None
    artifact_repository: str | None
    cloud_sql_instance: str | None
    database_name: str | None
    bucket_name: str | None
    image: str | None
    service_url: str | None
    service_account_email: str | None
    last_deployed_at_ms: int | None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "DeployState":
        def _read_str(key: str) -> str | None:
            value = payload.get(key)
            if value is None:
                return None
            text = str(value).strip()
            return text or None

        last_deployed_at_ms = payload.get("last_deployed_at_ms")
        return cls(
            project_id=_read_str("project_id"),
            region=_read_str("region"),
            service_name=_read_str("service_name"),
            artifact_repository=_read_str("artifact_repository"),
            cloud_sql_instance=_read_str("cloud_sql_instance"),
            database_name=_read_str("database_name"),
            bucket_name=_read_str("bucket_name"),
            image=_read_str("image"),
            service_url=_read_str("service_url"),
            service_account_email=_read_str("service_account_email"),
            last_deployed_at_ms=(
                int(last_deployed_at_ms) if isinstance(last_deployed_at_ms, int) else None
            ),
        )

    def has_data(self) -> bool:
        return bool(self.to_payload())

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {}
        for key, value in (
            ("project_id", self.project_id),
            ("region", self.region),
            ("service_name", self.service_name),
            ("artifact_repository", self.artifact_repository),
            ("cloud_sql_instance", self.cloud_sql_instance),
            ("database_name", self.database_name),
            ("bucket_name", self.bucket_name),
            ("image", self.image),
            ("service_url", self.service_url),
            ("service_account_email", self.service_account_email),
            ("last_deployed_at_ms", self.last_deployed_at_ms),
        ):
            if value is not None:
                payload[key] = value
        return payload


def read_deploy_state(path: Path) -> DeployState:
    payload = read_json_state(path)
    return DeployState.from_payload(payload)


def write_deploy_state(path: Path, state: DeployState) -> None:
    write_json_state(path, state.to_payload())
