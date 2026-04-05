from __future__ import annotations

from dataclasses import dataclass

from portworld_cli.gcp.executor import GCloudExecutor
from portworld_cli.gcp.types import GCPResult, MutationOutcome


@dataclass(frozen=True, slots=True)
class ServiceAccountRef:
    account_id: str
    email: str
    display_name: str | None = None


@dataclass(frozen=True, slots=True)
class IAMBindingRef:
    resource: str
    member: str
    role: str


class IAMAdapter:
    def __init__(self, executor: GCloudExecutor) -> None:
        self._executor = executor

    def get_service_account(self, *, project_id: str, email: str) -> GCPResult[ServiceAccountRef | None]:
        result = self._executor.run_json(
            [
                "iam",
                "service-accounts",
                "describe",
                email,
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
        return GCPResult.success(_service_account_from_payload(payload))

    def create_service_account(
        self,
        *,
        project_id: str,
        account_id: str,
        display_name: str,
    ) -> GCPResult[MutationOutcome[ServiceAccountRef]]:
        email = build_service_account_email(account_id=account_id, project_id=project_id)
        existing = self.get_service_account(project_id=project_id, email=email)
        if not existing.ok:
            return GCPResult.failure(existing.error)  # type: ignore[arg-type]
        if existing.value is not None:
            return GCPResult.success(MutationOutcome(action="existing", resource=existing.value))

        create_result = self._executor.run_text(
            [
                "iam",
                "service-accounts",
                "create",
                account_id,
                f"--project={project_id}",
                f"--display-name={display_name}",
            ]
        )
        if not create_result.ok:
            return GCPResult.failure(create_result.error)  # type: ignore[arg-type]

        described = self.get_service_account(project_id=project_id, email=email)
        if not described.ok:
            return GCPResult.failure(described.error)  # type: ignore[arg-type]
        resource = described.value or ServiceAccountRef(
            account_id=account_id,
            email=email,
            display_name=display_name,
        )
        return GCPResult.success(MutationOutcome(action="created", resource=resource))

    def bind_project_role(
        self,
        *,
        project_id: str,
        service_account_email: str,
        role: str,
    ) -> GCPResult[MutationOutcome[IAMBindingRef]]:
        member = f"serviceAccount:{service_account_email}"
        result = self._executor.run_text(
            [
                "projects",
                "add-iam-policy-binding",
                project_id,
                f"--member={member}",
                f"--role={role}",
            ]
        )
        if not result.ok:
            return GCPResult.failure(result.error)  # type: ignore[arg-type]
        return GCPResult.success(
            MutationOutcome(
                action="bound",
                resource=IAMBindingRef(resource=project_id, member=member, role=role),
            )
        )

    def bind_bucket_role(
        self,
        *,
        bucket_name: str,
        service_account_email: str,
        role: str,
    ) -> GCPResult[MutationOutcome[IAMBindingRef]]:
        member = f"serviceAccount:{service_account_email}"
        bucket_uri = f"gs://{bucket_name}"
        result = self._executor.run_text(
            [
                "storage",
                "buckets",
                "add-iam-policy-binding",
                bucket_uri,
                f"--member={member}",
                f"--role={role}",
            ]
        )
        if not result.ok:
            return GCPResult.failure(result.error)  # type: ignore[arg-type]
        return GCPResult.success(
            MutationOutcome(
                action="bound",
                resource=IAMBindingRef(resource=bucket_uri, member=member, role=role),
            )
        )


def build_service_account_email(*, account_id: str, project_id: str) -> str:
    return f"{account_id}@{project_id}.iam.gserviceaccount.com"


def _service_account_from_payload(payload: dict[str, object]) -> ServiceAccountRef:
    email = str(payload.get("email", "")).strip()
    account_id = email.split("@", 1)[0] if email else ""
    display_name = str(payload.get("displayName", "")).strip() or None
    return ServiceAccountRef(account_id=account_id, email=email, display_name=display_name)
