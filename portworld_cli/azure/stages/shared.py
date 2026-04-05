from __future__ import annotations

import hashlib
from time import time_ns


def stage_ok(stage: str, message: str) -> dict[str, object]:
    return {"stage": stage, "status": "ok", "message": message}


def now_ms() -> int:
    return time_ns() // 1_000_000


def stable_suffix(seed: str, *, length: int) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return digest[:length]


def sanitize_name_token(value: str) -> str:
    lowered = value.strip().lower()
    return "".join(char for char in lowered if char.isalnum())


def build_storage_account_name(app_name: str, suffix: str) -> str:
    token = sanitize_name_token(app_name) or "portworld"
    candidate = f"pw{token}{suffix}"
    return candidate[:24]


def build_acr_name(app_name: str, suffix: str) -> str:
    token = sanitize_name_token(app_name) or "portworld"
    candidate = f"pw{token}{suffix}"
    if len(candidate) < 5:
        candidate = f"{candidate}acr"
    return candidate[:50]


def build_postgres_server_name(app_name: str, suffix: str) -> str:
    token = sanitize_name_token(app_name) or "portworld"
    candidate = f"pwpg{token}{suffix}"
    if len(candidate) < 3:
        candidate = f"pwpg{suffix}"
    return candidate[:63]


def to_azure_secret_name(key: str) -> str:
    normalized = key.lower().replace("_", "-")
    normalized = "".join(char for char in normalized if char.isalnum() or char == "-")
    normalized = normalized.strip("-") or "secret"
    if len(normalized) > 63:
        normalized = normalized[:63].rstrip("-")
    return normalized or "secret"
