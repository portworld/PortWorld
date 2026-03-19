from __future__ import annotations

import ipaddress
import json
import re
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AWSCommandResult:
    ok: bool
    value: object | None
    message: str | None = None


def aws_cli_available() -> bool:
    return shutil.which("aws") is not None


def run_aws_json(args: list[str]) -> AWSCommandResult:
    completed = subprocess.run(
        ["aws", *args, "--output", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return AWSCommandResult(
            ok=False,
            value=None,
            message=(completed.stderr or completed.stdout).strip() or "AWS CLI command failed.",
        )
    text = (completed.stdout or "").strip()
    if not text:
        return AWSCommandResult(ok=True, value={})
    try:
        return AWSCommandResult(ok=True, value=json.loads(text))
    except json.JSONDecodeError:
        return AWSCommandResult(ok=False, value=None, message="AWS CLI returned non-JSON output.")


def run_aws_text(args: list[str]) -> AWSCommandResult:
    completed = subprocess.run(
        ["aws", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return AWSCommandResult(
            ok=False,
            value=None,
            message=(completed.stderr or completed.stdout).strip() or "AWS CLI command failed.",
        )
    return AWSCommandResult(ok=True, value=(completed.stdout or "").strip())


def normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def split_csv_values(raw_value: str | None) -> tuple[str, ...]:
    normalized = normalize_optional_text(raw_value)
    if normalized is None:
        return ()
    return tuple(part.strip() for part in normalized.split(",") if part.strip())


def validate_s3_bucket_name(name: str) -> str | None:
    if len(name) < 3 or len(name) > 63:
        return "S3 bucket name must be between 3 and 63 characters."
    if name.lower() != name:
        return "S3 bucket name must use lowercase characters only."
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*[a-z0-9]", name):
        return "S3 bucket name may contain only lowercase letters, numbers, dots, and hyphens."
    if ".." in name:
        return "S3 bucket name cannot contain adjacent periods."
    if ".-" in name or "-." in name:
        return "S3 bucket name cannot use dashes next to periods."
    if name.startswith("xn--"):
        return "S3 bucket name cannot start with the reserved prefix 'xn--'."
    if name.startswith("sthree-"):
        return "S3 bucket name cannot start with the reserved prefix 'sthree-'."
    if name.startswith("amzn-s3-demo-"):
        return "S3 bucket name cannot start with the reserved prefix 'amzn-s3-demo-'."
    if name.endswith("-s3alias"):
        return "S3 bucket name cannot end with the reserved suffix '-s3alias'."
    if name.endswith("--ol-s3"):
        return "S3 bucket name cannot end with the reserved suffix '--ol-s3'."
    if name.endswith(".mrap"):
        return "S3 bucket name cannot end with the reserved suffix '.mrap'."
    if name.endswith("--x-s3"):
        return "S3 bucket name cannot end with the reserved suffix '--x-s3'."
    if name.endswith("--table-s3"):
        return "S3 bucket name cannot end with the reserved suffix '--table-s3'."
    try:
        ipaddress.ip_address(name)
        return "S3 bucket name cannot be formatted as an IP address."
    except ValueError:
        return None


def s3_bucket_name_tls_warning(name: str) -> str | None:
    if "." in name:
        return (
            "S3 bucket name includes periods; virtual-hosted HTTPS certificate "
            "validation can fail. Prefer bucket names without periods."
        )
    return None


def is_postgres_url(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered.startswith("postgresql://") or lowered.startswith("postgres://")
