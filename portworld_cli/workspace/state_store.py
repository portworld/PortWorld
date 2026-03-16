from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any


class CLIStateError(RuntimeError):
    """Base error for CLI-managed state files."""


class CLIStateDecodeError(CLIStateError):
    """Raised when CLI-managed state is not valid JSON."""


class CLIStateTypeError(CLIStateError):
    """Raised when CLI-managed state is not a JSON object."""


def read_json_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CLIStateDecodeError(f"Failed to parse CLI state file: {path}") from exc

    if not isinstance(payload, dict):
        raise CLIStateTypeError(f"CLI state file must contain a JSON object: {path}")
    return payload


def write_json_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with open(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2, sort_keys=True)
            handle.write("\n")
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
