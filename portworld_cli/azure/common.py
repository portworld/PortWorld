from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class AzureCommandResult:
    ok: bool
    value: object | None
    message: str | None = None


def azure_cli_available() -> bool:
    return shutil.which("az") is not None


def run_az_json(args: list[str]) -> AzureCommandResult:
    completed = subprocess.run(
        ["az", *args, "-o", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return AzureCommandResult(
            ok=False,
            value=None,
            message=(completed.stderr or completed.stdout).strip() or "Azure CLI command failed.",
        )
    text = (completed.stdout or "").strip()
    if not text:
        return AzureCommandResult(ok=True, value={})
    try:
        return AzureCommandResult(ok=True, value=json.loads(text))
    except json.JSONDecodeError:
        return AzureCommandResult(ok=False, value=None, message="Azure CLI returned non-JSON output.")


def normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def is_postgres_url(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered.startswith("postgresql://") or lowered.startswith("postgres://")


def validate_storage_account_name(name: str) -> str | None:
    if len(name) < 3 or len(name) > 24:
        return "Azure storage account name must be between 3 and 24 characters."
    if not re.fullmatch(r"[a-z0-9]+", name):
        return "Azure storage account name must use lowercase letters and numbers only."
    return None


def validate_blob_container_name(name: str) -> str | None:
    if len(name) < 3 or len(name) > 63:
        return "Azure blob container name must be between 3 and 63 characters."
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", name):
        return "Azure blob container name must use lowercase letters, numbers, and hyphens, and start/end with alphanumeric characters."
    if "--" in name:
        return "Azure blob container name cannot contain consecutive hyphens."
    return None


def validate_blob_endpoint(endpoint: str) -> str | None:
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.netloc:
        return "Azure blob endpoint must be a valid https URL."
    return None


def read_dict_string(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None
