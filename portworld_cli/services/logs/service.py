from __future__ import annotations

from dataclasses import dataclass
import json

from portworld_cli.aws.common import run_aws_json, run_aws_text
from portworld_cli.azure.common import run_az_json, run_az_text
from portworld_cli.context import CLIContext
from portworld_cli.gcp import GCPAdapters, GCPError
from portworld_cli.output import CommandResult
from portworld_cli.services.common.error_mapping import ErrorMappingPolicy, map_command_exception
from portworld_cli.workspace.session import (
    load_inspection_session,
    resolve_aws_inspection_target,
    resolve_azure_inspection_target,
    resolve_gcp_inspection_target,
)


@dataclass(frozen=True, slots=True)
class LogsGCPCloudRunOptions:
    project: str | None
    region: str | None
    service: str | None
    since: str
    limit: int
    severity: str | None


@dataclass(frozen=True, slots=True)
class LogsAWSECSFargateOptions:
    region: str | None
    service: str | None
    since: str
    limit: int


@dataclass(frozen=True, slots=True)
class LogsAzureContainerAppsOptions:
    subscription: str | None
    resource_group: str | None
    app: str | None
    since: str
    limit: int


class LogsUsageError(RuntimeError):
    pass


def run_logs_gcp_cloud_run(
    cli_context: CLIContext,
    options: LogsGCPCloudRunOptions,
) -> CommandResult:
    command_name = "portworld logs gcp-cloud-run"
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
            policy=ErrorMappingPolicy(command_name=command_name),
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
        return _gcp_failure_result(error=result.error, command_name=command_name)

    entries = result.value or ()
    return CommandResult(
        ok=True,
        command=command_name,
        message=_build_logs_message(
            header=(
                f"target: gcp-cloud-run",
                f"project_id: {target.project_id}",
                f"region: {target.region}",
                f"service_name: {target.service_name}",
                f"since: {options.since}",
            ),
            lines=[
                _format_log_line(entry.timestamp, entry.severity, entry.revision_name, entry.message)
                for entry in entries
            ],
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


def run_logs_aws_ecs_fargate(
    cli_context: CLIContext,
    options: LogsAWSECSFargateOptions,
) -> CommandResult:
    command_name = "portworld logs aws-ecs-fargate"
    try:
        session = load_inspection_session(cli_context)
        if options.limit < 1:
            raise LogsUsageError("--limit must be at least 1.")
        target = resolve_aws_inspection_target(
            session,
            region=options.region,
            service_name=options.service,
        )
        if not target.region:
            raise LogsUsageError("Missing AWS region. Pass --region or configure it first.")
        if not target.service_name:
            raise LogsUsageError("Missing AWS ECS service name. Pass --service or configure it first.")
    except Exception as exc:
        return map_command_exception(
            exc,
            policy=ErrorMappingPolicy(command_name=command_name),
            usage_error_types=(LogsUsageError,),
        )

    log_group_name = f"/ecs/{target.service_name}"
    described = run_aws_json(
        [
            "logs",
            "describe-log-groups",
            "--region",
            target.region,
            "--log-group-name-prefix",
            log_group_name,
        ]
    )
    if not described.ok or not isinstance(described.value, dict):
        return CommandResult(
            ok=False,
            command=command_name,
            message=described.message or "Unable to inspect ECS CloudWatch log groups.",
            data={
                "target": "aws-ecs-fargate",
                "region": target.region,
                "service_name": target.service_name,
                "status": "error",
            },
            exit_code=1,
        )

    log_group_names = _extract_aws_log_group_names(described.value)
    if log_group_name not in log_group_names:
        return CommandResult(
            ok=True,
            command=command_name,
            message=_build_logs_message(
                header=(
                    "target: aws-ecs-fargate",
                    f"region: {target.region}",
                    f"service_name: {target.service_name}",
                    f"since: {options.since}",
                ),
                lines=[],
            ),
            data={
                "target": "aws-ecs-fargate",
                "region": target.region,
                "service_name": target.service_name,
                "since": options.since,
                "limit": options.limit,
                "log_groups": [],
                "entries": [],
            },
            exit_code=0,
        )

    rendered_entries: list[dict[str, object | None]] = []
    tailed = run_aws_text(
        [
            "logs",
            "tail",
            log_group_name,
            "--region",
            target.region,
            "--since",
            options.since.strip(),
            "--format",
            "short",
        ]
    )
    if not tailed.ok:
        return CommandResult(
            ok=False,
            command=command_name,
            message=tailed.message or f"Unable to read log group {log_group_name}.",
            data={
                "target": "aws-ecs-fargate",
                "region": target.region,
                "service_name": target.service_name,
                "log_group": log_group_name,
                "status": "error",
            },
            exit_code=1,
        )
    entries = _parse_text_log_entries(
        tailed.value if isinstance(tailed.value, str) else "",
        source=log_group_name,
    )
    rendered_entries.extend(entries)

    rendered_entries = rendered_entries[-options.limit :]
    rendered_lines = [
        _format_log_line(
            entry.get("timestamp"),
            entry.get("severity"),
            entry.get("source"),
            entry.get("message") or "",
        )
        for entry in rendered_entries
    ]
    return CommandResult(
        ok=True,
        command=command_name,
        message=_build_logs_message(
            header=(
                "target: aws-ecs-fargate",
                f"region: {target.region}",
                f"service_name: {target.service_name}",
                f"since: {options.since}",
            ),
            lines=rendered_lines,
        ),
        data={
            "target": "aws-ecs-fargate",
            "region": target.region,
            "service_name": target.service_name,
            "since": options.since,
            "limit": options.limit,
            "log_groups": [log_group_name],
            "entries": rendered_entries,
        },
        exit_code=0,
    )
def run_logs_azure_container_apps(
    cli_context: CLIContext,
    options: LogsAzureContainerAppsOptions,
) -> CommandResult:
    command_name = "portworld logs azure-container-apps"
    try:
        session = load_inspection_session(cli_context)
        if options.limit < 1:
            raise LogsUsageError("--limit must be at least 1.")
        target = resolve_azure_inspection_target(
            session,
            subscription_id=options.subscription,
            resource_group=options.resource_group,
            app_name=options.app,
        )
        if not target.subscription_id:
            raise LogsUsageError("Missing Azure subscription id. Pass --subscription or configure it first.")
        if not target.resource_group:
            raise LogsUsageError("Missing Azure resource group. Pass --resource-group or configure it first.")
        if not target.app_name:
            raise LogsUsageError("Missing Azure Container App name. Pass --app or configure it first.")
    except Exception as exc:
        return map_command_exception(
            exc,
            policy=ErrorMappingPolicy(command_name=command_name),
            usage_error_types=(LogsUsageError,),
        )

    log_args = [
        "containerapp",
        "logs",
        "show",
        "--subscription",
        target.subscription_id,
        "--resource-group",
        target.resource_group,
        "--name",
        target.app_name,
        "--tail",
        str(options.limit),
    ]
    log_result = run_az_json(log_args)
    entries: list[dict[str, object | None]] = []
    if log_result.ok:
        entries = _parse_azure_log_entries(log_result.value)
    else:
        fallback = run_az_text(log_args)
        if not fallback.ok:
            return CommandResult(
                ok=False,
                command=command_name,
                message=fallback.message or "Unable to read Azure Container Apps logs.",
                data={
                    "target": "azure-container-apps",
                    "subscription_id": target.subscription_id,
                    "resource_group": target.resource_group,
                    "app_name": target.app_name,
                    "status": "error",
                },
                exit_code=1,
            )
        entries = _parse_text_log_entries(
            fallback.value if isinstance(fallback.value, str) else "",
            source=target.app_name,
        )

    entries = entries[-options.limit :]
    lines = [
        _format_log_line(
            entry.get("timestamp"),
            entry.get("severity"),
            entry.get("source"),
            entry.get("message") or "",
        )
        for entry in entries
    ]
    return CommandResult(
        ok=True,
        command=command_name,
        message=_build_logs_message(
            header=(
                "target: azure-container-apps",
                f"subscription_id: {target.subscription_id}",
                f"resource_group: {target.resource_group}",
                f"app_name: {target.app_name}",
                f"since: {options.since}",
            ),
            lines=lines,
        ),
        data={
            "target": "azure-container-apps",
            "subscription_id": target.subscription_id,
            "resource_group": target.resource_group,
            "app_name": target.app_name,
            "since": options.since,
            "limit": options.limit,
            "entries": entries,
        },
        exit_code=0,
    )


def _extract_aws_log_group_names(payload: dict[str, object]) -> list[str]:
    groups = payload.get("logGroups")
    if not isinstance(groups, list):
        return []
    values: list[str] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        value = group.get("logGroupName")
        if not isinstance(value, str):
            continue
        text = value.strip()
        if text:
            values.append(text)
    values.sort()
    return values


def _parse_azure_log_entries(payload: object) -> list[dict[str, object | None]]:
    if isinstance(payload, dict):
        payload = payload.get("logs") or payload.get("value") or [payload]
    entries: list[dict[str, object | None]] = []
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, str):
                entries.append(_simple_log_entry(item))
                continue
            if not isinstance(item, dict):
                continue
            message = _first_string(
                item,
                "message",
                "log",
                "text",
                "msg",
            )
            timestamp = _first_string(
                item,
                "time",
                "timestamp",
                "TimeStamp",
            )
            entries.append(
                {
                    "timestamp": timestamp,
                    "severity": _first_string(item, "level", "severity"),
                    "source": _first_string(item, "containerName", "stream"),
                    "message": message or json.dumps(item, ensure_ascii=True, sort_keys=True),
                }
            )
    return entries


def _parse_text_log_entries(raw_text: str, *, source: str) -> list[dict[str, object | None]]:
    entries: list[dict[str, object | None]] = []
    for line in raw_text.splitlines():
        text = line.strip()
        if not text:
            continue
        timestamp, message = _split_timestamp_prefix(text)
        entries.append(
            {
                "timestamp": timestamp,
                "severity": None,
                "source": source,
                "message": message,
            }
        )
    return entries


def _simple_log_entry(message: str) -> dict[str, object | None]:
    timestamp, normalized_message = _split_timestamp_prefix(message.strip())
    return {
        "timestamp": timestamp,
        "severity": None,
        "source": None,
        "message": normalized_message,
    }


def _split_timestamp_prefix(text: str) -> tuple[str | None, str]:
    if " " not in text:
        return None, text
    first_token, remainder = text.split(" ", 1)
    if "T" in first_token or first_token.count("-") == 2:
        return first_token, remainder.strip()
    return None, text


def _first_string(payload: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
    return None


def _build_logs_message(*, header: tuple[str, ...], lines: list[str]) -> str:
    if not lines:
        return "\n".join([*header, "", "No log entries found."])
    return "\n".join([*header, "", *lines])


def _format_log_line(
    timestamp: object,
    severity: object,
    source: object,
    message: str,
) -> str:
    return " ".join(
        [
            str(timestamp or "-"),
            str(severity or "-"),
            str(source or "-"),
            " ".join(message.splitlines()).strip() or "(empty message)",
        ]
    )


def _gcp_failure_result(*, error: GCPError, command_name: str) -> CommandResult:
    payload: dict[str, object] = {
        "status": "error",
        "error_type": "GCPError",
        "error_code": error.code,
    }
    if error.command is not None:
        payload["gcloud_command"] = error.command
    return CommandResult(
        ok=False,
        command=command_name,
        message=error.message,
        data=payload,
        exit_code=1,
    )
