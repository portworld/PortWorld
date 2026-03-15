from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from backend.cli_app.gcp.executor import GCloudExecutor
from backend.cli_app.gcp.types import GCPResult, MutationOutcome


@dataclass(frozen=True, slots=True)
class CloudSQLInstanceRef:
    project_id: str
    region: str
    instance_name: str
    database_version: str | None = None
    connection_name: str | None = None
    primary_ip_address: str | None = None


@dataclass(frozen=True, slots=True)
class CloudSQLDatabaseRef:
    instance_name: str
    database_name: str


@dataclass(frozen=True, slots=True)
class CloudSQLUserRef:
    instance_name: str
    user_name: str
    host: str


class CloudSQLAdapter:
    def __init__(self, executor: GCloudExecutor) -> None:
        self._executor = executor

    def get_instance(self, *, project_id: str, instance_name: str) -> GCPResult[CloudSQLInstanceRef | None]:
        result = self._executor.run_json(
            [
                "sql",
                "instances",
                "describe",
                instance_name,
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
        return GCPResult.success(_instance_from_payload(payload, project_id=project_id))

    def create_instance(
        self,
        *,
        project_id: str,
        region: str,
        instance_name: str,
        database_version: str,
        cpu_count: int,
        memory: str,
    ) -> GCPResult[MutationOutcome[CloudSQLInstanceRef]]:
        existing = self.get_instance(project_id=project_id, instance_name=instance_name)
        if not existing.ok:
            return GCPResult.failure(existing.error)  # type: ignore[arg-type]
        if existing.value is not None:
            return GCPResult.success(MutationOutcome(action="existing", resource=existing.value))

        result = self._executor.run_text(
            [
                "sql",
                "instances",
                "create",
                instance_name,
                f"--project={project_id}",
                f"--database-version={database_version}",
                "--edition=ENTERPRISE",
                f"--cpu={cpu_count}",
                f"--memory={memory}",
                f"--region={region}",
            ],
            timeout_seconds=self._executor.long_timeout_seconds,
        )
        if not result.ok:
            return GCPResult.failure(result.error)  # type: ignore[arg-type]

        described = self.get_instance(project_id=project_id, instance_name=instance_name)
        if not described.ok:
            return GCPResult.failure(described.error)  # type: ignore[arg-type]
        resource = described.value or CloudSQLInstanceRef(
            project_id=project_id,
            region=region,
            instance_name=instance_name,
            database_version=database_version,
            connection_name=None,
            primary_ip_address=None,
        )
        return GCPResult.success(MutationOutcome(action="created", resource=resource))

    def get_database(
        self,
        *,
        project_id: str,
        instance_name: str,
        database_name: str,
    ) -> GCPResult[CloudSQLDatabaseRef | None]:
        result = self._executor.run_json(
            [
                "sql",
                "databases",
                "describe",
                database_name,
                f"--instance={instance_name}",
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
        return GCPResult.success(
            CloudSQLDatabaseRef(instance_name=instance_name, database_name=database_name)
        )

    def create_database(
        self,
        *,
        project_id: str,
        instance_name: str,
        database_name: str,
    ) -> GCPResult[MutationOutcome[CloudSQLDatabaseRef]]:
        existing = self.get_database(
            project_id=project_id,
            instance_name=instance_name,
            database_name=database_name,
        )
        if not existing.ok:
            return GCPResult.failure(existing.error)  # type: ignore[arg-type]
        if existing.value is not None:
            return GCPResult.success(MutationOutcome(action="existing", resource=existing.value))

        result = self._executor.run_text(
            [
                "sql",
                "databases",
                "create",
                database_name,
                f"--instance={instance_name}",
                f"--project={project_id}",
            ]
        )
        if not result.ok:
            return GCPResult.failure(result.error)  # type: ignore[arg-type]
        return GCPResult.success(
            MutationOutcome(
                action="created",
                resource=CloudSQLDatabaseRef(instance_name=instance_name, database_name=database_name),
            )
        )

    def get_user(
        self,
        *,
        project_id: str,
        instance_name: str,
        user_name: str,
        host: str = "%",
    ) -> GCPResult[CloudSQLUserRef | None]:
        result = self._executor.run_json(
            [
                "sql",
                "users",
                "list",
                f"--instance={instance_name}",
                f"--project={project_id}",
                "--format=json",
            ]
        )
        if not result.ok:
            return GCPResult.failure(result.error)  # type: ignore[arg-type]
        payload = result.value or []
        if not isinstance(payload, list):
            return GCPResult.success(None)
        for item in payload:
            if not isinstance(item, dict):
                continue
            if str(item.get("name", "")).strip() != user_name:
                continue
            item_host = str(item.get("host", "%")).strip() or "%"
            if item_host == host:
                return GCPResult.success(
                    CloudSQLUserRef(instance_name=instance_name, user_name=user_name, host=item_host)
                )
        return GCPResult.success(None)

    def create_or_update_user(
        self,
        *,
        project_id: str,
        instance_name: str,
        user_name: str,
        password: str,
        host: str = "%",
    ) -> GCPResult[MutationOutcome[CloudSQLUserRef]]:
        existing = self.get_user(
            project_id=project_id,
            instance_name=instance_name,
            user_name=user_name,
            host=host,
        )
        if not existing.ok:
            return GCPResult.failure(existing.error)  # type: ignore[arg-type]

        if existing.value is None:
            result = self._executor.run_text(
                [
                    "sql",
                    "users",
                    "create",
                    user_name,
                    f"--instance={instance_name}",
                    f"--project={project_id}",
                    f"--host={host}",
                    f"--password={password}",
                ],
                display_args=[
                    "sql",
                    "users",
                    "create",
                    user_name,
                    f"--instance={instance_name}",
                    f"--project={project_id}",
                    f"--host={host}",
                    "--password=REDACTED",
                ],
            )
            if not result.ok:
                return GCPResult.failure(result.error)  # type: ignore[arg-type]
            return GCPResult.success(
                MutationOutcome(
                    action="created",
                    resource=CloudSQLUserRef(instance_name=instance_name, user_name=user_name, host=host),
                )
            )

        result = self._executor.run_text(
            [
                "sql",
                "users",
                "set-password",
                user_name,
                f"--instance={instance_name}",
                f"--project={project_id}",
                f"--host={host}",
                f"--password={password}",
            ],
            display_args=[
                "sql",
                "users",
                "set-password",
                user_name,
                f"--instance={instance_name}",
                f"--project={project_id}",
                f"--host={host}",
                "--password=REDACTED",
            ],
        )
        if not result.ok:
            return GCPResult.failure(result.error)  # type: ignore[arg-type]
        return GCPResult.success(
            MutationOutcome(
                action="updated",
                resource=CloudSQLUserRef(instance_name=instance_name, user_name=user_name, host=host),
            )
        )


def build_postgres_url(
    *,
    username: str,
    password: str,
    database_name: str,
    host: str,
    port: int = 5432,
) -> str:
    quoted_user = quote(username, safe="")
    quoted_password = quote(password, safe="")
    quoted_database = quote(database_name, safe="")
    return f"postgresql://{quoted_user}:{quoted_password}@{host}:{port}/{quoted_database}"


def _instance_from_payload(payload: dict[str, object], *, project_id: str) -> CloudSQLInstanceRef:
    primary_ip_address = None
    ip_addresses = payload.get("ipAddresses")
    if isinstance(ip_addresses, list):
        for item in ip_addresses:
            if not isinstance(item, dict):
                continue
            candidate_ip = str(item.get("ipAddress", "")).strip() or None
            candidate_type = str(item.get("type", "")).strip().upper()
            if candidate_ip is None:
                continue
            if candidate_type == "PRIMARY":
                primary_ip_address = candidate_ip
                break
            if primary_ip_address is None:
                primary_ip_address = candidate_ip
    return CloudSQLInstanceRef(
        project_id=project_id,
        region=str(payload.get("region", "")).strip(),
        instance_name=str(payload.get("name", "")).strip(),
        database_version=str(payload.get("databaseVersion", "")).strip() or None,
        connection_name=str(payload.get("connectionName", "")).strip() or None,
        primary_ip_address=primary_ip_address,
    )
