from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tempfile

from backend.cli_app.gcp.executor import GCloudExecutor
from backend.cli_app.gcp.types import GCPResult


@dataclass(frozen=True, slots=True)
class CloudBuildSubmission:
    build_id: str | None
    image_uri: str
    log_url: str | None = None


class CloudBuildAdapter:
    def __init__(self, executor: GCloudExecutor) -> None:
        self._executor = executor

    def submit_build(
        self,
        *,
        project_id: str,
        source_dir: Path,
        dockerfile_path: Path,
        image_uri: str,
    ) -> GCPResult[CloudBuildSubmission]:
        dockerfile_relative_path = dockerfile_path.relative_to(source_dir)
        cloudbuild_yaml = "\n".join(
            [
                "steps:",
                "- name: gcr.io/cloud-builders/docker",
                "  args:",
                "  - build",
                "  - -f",
                f"  - {dockerfile_relative_path.as_posix()}",
                "  - -t",
                f"  - {image_uri}",
                "  - .",
                "images:",
                f"- {image_uri}",
                "",
            ]
        )

        with tempfile.NamedTemporaryFile(
            mode="w",
            prefix="portworld-cloudbuild-",
            suffix=".yaml",
            dir=source_dir,
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(cloudbuild_yaml)
            config_path = Path(handle.name)

        try:
            result = self._executor.run_text(
                [
                    "builds",
                    "submit",
                    str(source_dir),
                    f"--project={project_id}",
                    f"--config={config_path}",
                ],
                cwd=source_dir,
                timeout_seconds=self._executor.long_timeout_seconds,
            )
        finally:
            config_path.unlink(missing_ok=True)

        if not result.ok:
            return GCPResult.failure(result.error)  # type: ignore[arg-type]
        output = result.value
        assert output is not None
        return GCPResult.success(
            CloudBuildSubmission(
                build_id=_extract_build_id(output.stdout),
                image_uri=image_uri,
                log_url=_extract_log_url(output.stdout),
            )
        )


def _extract_build_id(stdout: str) -> str | None:
    for line in stdout.splitlines():
        normalized = line.strip()
        if normalized.lower().startswith("id "):
            parts = normalized.split()
            if len(parts) >= 2:
                return parts[1]
    return None


def _extract_log_url(stdout: str) -> str | None:
    for line in stdout.splitlines():
        normalized = line.strip()
        if normalized.startswith("https://"):
            return normalized
    return None
