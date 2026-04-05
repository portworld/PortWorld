from __future__ import annotations

from dataclasses import dataclass
import json
from urllib.parse import unquote

from portworld_cli.gcp.executor import GCloudExecutor
from portworld_cli.gcp.types import GCPResult


@dataclass(frozen=True, slots=True)
class CloudRunLogEntry:
    timestamp: str | None
    severity: str | None
    log_name: str | None
    service_name: str | None
    revision_name: str | None
    message: str
    insert_id: str | None
    trace: str | None

    def to_payload(self) -> dict[str, object | None]:
        return {
            "timestamp": self.timestamp,
            "severity": self.severity,
            "log_name": self.log_name,
            "service_name": self.service_name,
            "revision_name": self.revision_name,
            "message": self.message,
            "insert_id": self.insert_id,
            "trace": self.trace,
        }


class GCPLoggingAdapter:
    def __init__(self, executor: GCloudExecutor) -> None:
        self._executor = executor

    def read_cloud_run_logs(
        self,
        *,
        project_id: str,
        region: str,
        service_name: str,
        since: str,
        limit: int,
        severity: str | None,
    ) -> GCPResult[tuple[CloudRunLogEntry, ...]]:
        filters = [
            'resource.type="cloud_run_revision"',
            f'resource.labels.service_name="{service_name}"',
            f'resource.labels.location="{region}"',
        ]
        if severity:
            filters.append(f"severity>={severity}")

        result = self._executor.run_json(
            [
                "logging",
                "read",
                " AND ".join(filters),
                f"--project={project_id}",
                f"--limit={max(1, limit)}",
                f"--freshness={since}",
                "--order=desc",
                "--format=json",
            ]
        )
        if not result.ok:
            return GCPResult.failure(result.error)  # type: ignore[arg-type]

        payload = result.value
        if not isinstance(payload, list):
            return GCPResult.success(())

        entries: list[CloudRunLogEntry] = []
        for raw_entry in payload:
            if isinstance(raw_entry, dict):
                entries.append(_entry_from_payload(raw_entry))
        return GCPResult.success(tuple(entries))


def _entry_from_payload(payload: dict[str, object]) -> CloudRunLogEntry:
    resource = payload.get("resource")
    labels = resource.get("labels") if isinstance(resource, dict) else None

    return CloudRunLogEntry(
        timestamp=_read_str(payload.get("timestamp")),
        severity=_read_str(payload.get("severity")),
        log_name=_normalize_log_name(_read_str(payload.get("logName"))),
        service_name=_read_str(labels.get("service_name")) if isinstance(labels, dict) else None,
        revision_name=_read_str(labels.get("revision_name")) if isinstance(labels, dict) else None,
        message=_extract_message(payload),
        insert_id=_read_str(payload.get("insertId")),
        trace=_read_str(payload.get("trace")),
    )


def _extract_message(payload: dict[str, object]) -> str:
    text_payload = payload.get("textPayload")
    if isinstance(text_payload, str):
        text = text_payload.strip()
        if text:
            return text

    for key in ("jsonPayload", "protoPayload"):
        value = payload.get(key)
        if isinstance(value, dict):
            rendered = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
            if rendered:
                return rendered
        if isinstance(value, list):
            rendered = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
            if rendered:
                return rendered

    return ""


def _normalize_log_name(value: str | None) -> str | None:
    if value is None:
        return None
    if "/logs/" not in value:
        return value
    _, suffix = value.rsplit("/logs/", 1)
    return unquote(suffix)


def _read_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
