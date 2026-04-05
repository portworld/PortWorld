from __future__ import annotations

from pathlib import Path
import subprocess
from time import time_ns

from portworld_cli.gcp.cloud_build import CloudBuildSubmission
from portworld_cli.gcp import GCPAdapters
from portworld_cli.gcp.types import GCPResult


def resolve_source_image_tag(
    *,
    explicit_tag: str | None,
    project_root: Path,
) -> str:
    normalized_explicit = _normalize_text(explicit_tag)
    if normalized_explicit is not None:
        return normalized_explicit
    return _default_image_tag(project_root=project_root)


def submit_source_build(
    *,
    adapters: GCPAdapters,
    project_root: Path,
    dockerfile_path: Path,
    project_id: str,
    image_uri: str,
) -> GCPResult[CloudBuildSubmission]:
    return adapters.cloud_build.submit_build(
        project_id=project_id,
        source_dir=project_root,
        dockerfile_path=dockerfile_path,
        image_uri=image_uri,
    )


def _default_image_tag(*, project_root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
            cwd=str(project_root),
        )
    except Exception:
        completed = None
    if completed is not None and completed.returncode == 0:
        sha = completed.stdout.strip()
        if sha:
            return sha
    return str(_now_ms())


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _now_ms() -> int:
    return time_ns() // 1_000_000
