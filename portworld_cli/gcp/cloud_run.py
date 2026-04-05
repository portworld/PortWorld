from __future__ import annotations

from dataclasses import dataclass

from portworld_cli.gcp.executor import GCloudExecutor
from portworld_cli.gcp.types import GCPResult, MutationOutcome


@dataclass(frozen=True, slots=True)
class CloudRunServiceRef:
    project_id: str
    region: str
    service_name: str
    url: str | None
    image: str | None
    service_account_email: str | None
    ingress: str | None
    cloudsql_connection_name: str | None = None


class CloudRunAdapter:
    def __init__(self, executor: GCloudExecutor) -> None:
        self._executor = executor

    def get_service(
        self,
        *,
        project_id: str,
        region: str,
        service_name: str,
    ) -> GCPResult[CloudRunServiceRef | None]:
        result = self._executor.run_json(
            [
                "run",
                "services",
                "describe",
                service_name,
                f"--region={region}",
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
        return GCPResult.success(_service_from_payload(payload, project_id=project_id, region=region))

    def deploy_service(
        self,
        *,
        project_id: str,
        region: str,
        service_name: str,
        image_uri: str,
        service_account_email: str,
        env_vars: dict[str, str],
        secrets: dict[str, str],
        cloudsql_connection_name: str | None,
        timeout: str,
        cpu: str,
        memory: str,
        min_instances: int,
        max_instances: int,
        concurrency: int,
        allow_unauthenticated: bool,
        ingress: str,
    ) -> GCPResult[MutationOutcome[CloudRunServiceRef]]:
        existing = self.get_service(
            project_id=project_id,
            region=region,
            service_name=service_name,
        )
        if not existing.ok:
            return GCPResult.failure(existing.error)  # type: ignore[arg-type]

        args = [
            "run",
            "deploy",
            service_name,
            f"--project={project_id}",
            f"--region={region}",
            f"--image={image_uri}",
            f"--service-account={service_account_email}",
            f"--timeout={timeout}",
            f"--cpu={cpu}",
            f"--memory={memory}",
            f"--min-instances={max(0, min_instances)}",
            f"--max-instances={max(1, max_instances)}",
            f"--concurrency={max(1, concurrency)}",
            f"--ingress={ingress}",
            "--format=json",
        ]
        if allow_unauthenticated:
            args.append("--allow-unauthenticated")
        else:
            args.append("--no-allow-unauthenticated")
        if cloudsql_connection_name:
            args.append(f"--add-cloudsql-instances={cloudsql_connection_name}")
        if env_vars:
            args.append(f"--set-env-vars={_join_gcloud_pairs(env_vars)}")
        if secrets:
            args.append(f"--set-secrets={_join_gcloud_pairs(secrets)}")

        result = self._executor.run_json(
            args,
            timeout_seconds=self._executor.long_timeout_seconds,
        )
        if not result.ok:
            return GCPResult.failure(result.error)  # type: ignore[arg-type]

        payload = result.value
        if isinstance(payload, dict):
            resource = _service_from_payload(payload, project_id=project_id, region=region)
        else:
            resource = CloudRunServiceRef(
                project_id=project_id,
                region=region,
                service_name=service_name,
                url=None,
                image=image_uri,
                service_account_email=service_account_email,
                ingress=ingress,
                cloudsql_connection_name=cloudsql_connection_name,
            )
        return GCPResult.success(
            MutationOutcome(
                action="created" if existing.value is None else "updated",
                resource=resource,
            )
        )


def _join_gcloud_pairs(values: dict[str, str]) -> str:
    delimiter = "@"
    escaped_items: list[str] = []
    for key, value in values.items():
        normalized_value = value.replace(delimiter, f"\\{delimiter}")
        escaped_items.append(f"{key}={normalized_value}")
    return f"^{delimiter}^" + delimiter.join(escaped_items)


def _service_from_payload(
    payload: dict[str, object],
    *,
    project_id: str,
    region: str,
) -> CloudRunServiceRef:
    metadata = payload.get("metadata")
    spec = payload.get("spec")
    status = payload.get("status")

    service_name = ""
    if isinstance(metadata, dict):
        service_name = str(metadata.get("name", "")).strip()
    if not service_name:
        service_name = str(payload.get("serviceName", "")).strip()

    url = None
    if isinstance(status, dict):
        url = str(status.get("url", "")).strip() or None

    image = None
    service_account_email = None
    ingress = None
    cloudsql_connection_name = None
    if isinstance(spec, dict):
        template = spec.get("template")
        if isinstance(template, dict):
            template_metadata = template.get("metadata")
            if isinstance(template_metadata, dict):
                annotations = template_metadata.get("annotations")
                if isinstance(annotations, dict):
                    cloudsql_connection_name = (
                        str(
                            annotations.get(
                                "run.googleapis.com/cloudsql-instances",
                                "",
                            )
                        ).strip()
                        or None
                    )
            template_spec = template.get("spec")
            if isinstance(template_spec, dict):
                service_account_email = (
                    str(template_spec.get("serviceAccountName", "")).strip() or None
                )
                containers = template_spec.get("containers")
                if isinstance(containers, list) and containers:
                    first = containers[0]
                    if isinstance(first, dict):
                        image = str(first.get("image", "")).strip() or None
        ingress = str(spec.get("ingress", "")).strip() or None

    return CloudRunServiceRef(
        project_id=project_id,
        region=region,
        service_name=service_name,
        url=url,
        image=image,
        service_account_email=service_account_email,
        ingress=ingress,
        cloudsql_connection_name=cloudsql_connection_name,
    )
