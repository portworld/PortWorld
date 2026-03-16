from __future__ import annotations

from dataclasses import dataclass

from portworld_cli.context import CLIContext
from portworld_cli.gcp import GCPAdapters, GCPError
from portworld_cli.output import CommandResult
from portworld_cli.services.common.error_mapping import ErrorMappingPolicy, map_command_exception
from portworld_cli.workspace.session import load_inspection_session, resolve_gcp_inspection_target


COMMAND_NAME = "portworld logs gcp-cloud-run"


@dataclass(frozen=True, slots=True)
class LogsGCPCloudRunOptions:
    project: str | None
    region: str | None
    service: str | None
    since: str
    limit: int
    severity: str | None


class LogsUsageError(RuntimeError):
    pass


def run_logs_gcp_cloud_run(
    cli_context: CLIContext,
    options: LogsGCPCloudRunOptions,
) -> CommandResult:
    try:
        session = load_inspection_session(cli_context)
        if options.limit < 1:
            raise LogsUsageError("--limit must be at least 1.")
        target = resolve_gcp_inspection_target(
            session,
            project_id=options.project,
            region=options.region,
            service_name=options.service,
        )
        if not target.project_id:
            raise LogsUsageError("Missing GCP project id. Pass --project or configure it first.")
        if not target.region:
            raise LogsUsageError("Missing GCP region. Pass --region or configure it first.")
        if not target.service_name:
            raise LogsUsageError("Missing Cloud Run service name. Pass --service or configure it first.")
    except Exception as exc:
        return map_command_exception(
            exc,
            policy=ErrorMappingPolicy(command_name=COMMAND_NAME),
            usage_error_types=(LogsUsageError,),
        )

    adapters = GCPAdapters.create()
    result = adapters.logging.read_cloud_run_logs(
        project_id=target.project_id,
        region=target.region,
        service_name=target.service_name,
        since=options.since.strip(),
        limit=options.limit,
        severity=options.severity,
    )
    if not result.ok:
        assert result.error is not None
        return _gcp_failure_result(result.error)

    entries = result.value or ()
    return CommandResult(
        ok=True,
        command=COMMAND_NAME,
        message=_build_logs_message(
            project_id=target.project_id,
            region=target.region,
            service_name=target.service_name,
            since=options.since,
            entries=entries,
        ),
        data={
            "target": "gcp-cloud-run",
            "project_id": target.project_id,
            "region": target.region,
            "service_name": target.service_name,
            "since": options.since,
            "limit": options.limit,
            "severity": options.severity,
            "entries": [entry.to_payload() for entry in entries],
        },
        exit_code=0,
    )


def _build_logs_message(
    *,
    project_id: str,
    region: str,
    service_name: str,
    since: str,
    entries,
) -> str:
    header = [
        f"project_id: {project_id}",
        f"region: {region}",
        f"service_name: {service_name}",
        f"since: {since}",
    ]
    if not entries:
        return "\n".join(header + ["", "No log entries found."])

    lines = [
        _format_log_line(entry.timestamp, entry.severity, entry.revision_name, entry.message)
        for entry in entries
    ]
    return "\n".join(header + ["", *lines])


def _format_log_line(
    timestamp: str | None,
    severity: str | None,
    revision_name: str | None,
    message: str,
) -> str:
    rendered_message = " ".join(message.splitlines()).strip() or "(empty message)"
    return " ".join(
        [
            timestamp or "-",
            severity or "-",
            revision_name or "-",
            rendered_message,
        ]
    )


def _gcp_failure_result(error: GCPError) -> CommandResult:
    payload: dict[str, object] = {
        "status": "error",
        "error_type": "GCPError",
        "error_code": error.code,
    }
    if error.command is not None:
        payload["gcloud_command"] = error.command
    return CommandResult(
        ok=False,
        command=COMMAND_NAME,
        message=error.message,
        data=payload,
        exit_code=1,
    )
