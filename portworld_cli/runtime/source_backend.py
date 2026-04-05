from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from portworld_cli.workspace.discovery.paths import ProjectPaths


def run_source_backend_cli(
    paths: ProjectPaths,
    *,
    backend_args: list[str],
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        "-m",
        "backend.cli",
        "--env-file",
        str(paths.env_file),
        *backend_args,
    ]
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        cwd=paths.project_root,
    )


def coerce_source_backend_payload(
    completed: subprocess.CompletedProcess[str],
    *,
    default_message: str,
) -> dict[str, Any]:
    stdout = (completed.stdout or "").strip()
    if stdout:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(payload, dict):
                return payload

    message = (completed.stderr or completed.stdout or "").strip() or default_message
    return {
        "status": "error",
        "message": message,
    }


def build_source_backend_output_path(output_path: Path | None) -> Path:
    if output_path is not None:
        return output_path.resolve()
    return (Path.cwd() / f"portworld-memory-export-{_now_ms()}.zip").resolve()


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)
