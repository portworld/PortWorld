from __future__ import annotations

import json
import secrets
import string
from time import time_ns


def stage_ok(stage: str, message: str) -> dict[str, object]:
    return {"stage": stage, "status": "ok", "message": message}


def to_json_argument(payload: object) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def read_dict_string(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def normalize_service_url(value: str) -> str:
    text = value.strip()
    if text.startswith("https://"):
        return text
    if text.startswith("http://"):
        return "https://" + text[len("http://") :]
    return f"https://{text}"


def now_ms() -> int:
    return time_ns() // 1_000_000


def generate_rds_password() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(28))
