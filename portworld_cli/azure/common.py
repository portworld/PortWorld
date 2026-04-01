from __future__ import annotations

import re
import shutil
from urllib.parse import urlparse

def azure_cli_available() -> bool:
    return shutil.which("az") is not None


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
