from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from portworld_cli.gcp.types import CommandOutput, GCPError, GCPResult


class GCloudExecutor:
    def __init__(
        self,
        *,
        binary: str = "gcloud",
        default_timeout_seconds: float = 20.0,
        long_timeout_seconds: float = 1800.0,
    ) -> None:
        self.binary = binary
        self.default_timeout_seconds = default_timeout_seconds
        self.long_timeout_seconds = long_timeout_seconds

    def run_json(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        timeout_seconds: float | None = None,
        input_text: str | None = None,
        display_args: list[str] | None = None,
    ) -> GCPResult[Any]:
        command_result = self.run_text(
            args,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            input_text=input_text,
            display_args=display_args,
        )
        if not command_result.ok:
            return GCPResult.failure(command_result.error)  # type: ignore[arg-type]

        output = command_result.value
        assert output is not None
        stdout = output.stdout.strip()
        if not stdout:
            return GCPResult.success(None)
        try:
            return GCPResult.success(json.loads(stdout))
        except json.JSONDecodeError as exc:
            return GCPResult.failure(
                GCPError(
                    code="parse_error",
                    message="gcloud returned invalid JSON output.",
                    action="Re-run the command with --verbose and inspect the raw gcloud output.",
                    command=output.command,
                    exit_code=output.exit_code,
                    stderr=(output.stderr or str(exc)).strip() or None,
                )
            )

    def run_text(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        timeout_seconds: float | None = None,
        input_text: str | None = None,
        display_args: list[str] | None = None,
    ) -> GCPResult[CommandOutput]:
        if shutil.which(self.binary) is None:
            return GCPResult.failure(
                GCPError(
                    code="binary_missing",
                    message=f"{self.binary} is not installed or not on PATH.",
                    action="Install the Google Cloud SDK and make `gcloud` available on PATH.",
                    command=self.binary,
                )
            )

        full_command = [self.binary, "--quiet", *args]
        display_command_args = display_args if display_args is not None else args
        display_command = " ".join([self.binary, "--quiet", *display_command_args])
        env = os.environ.copy()
        env["CLOUDSDK_CORE_DISABLE_PROMPTS"] = "1"

        try:
            completed = subprocess.run(
                full_command,
                capture_output=True,
                text=True,
                check=False,
                cwd=str(cwd) if cwd is not None else None,
                env=env,
                input=input_text,
                timeout=timeout_seconds or self.default_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return GCPResult.failure(
                GCPError(
                    code="timeout",
                    message="gcloud command timed out.",
                    action="Retry the command or increase the timeout for long-running operations.",
                    command=display_command,
                )
            )
        except OSError as exc:
            return GCPResult.failure(
                GCPError(
                    code="command_failed",
                    message=str(exc),
                    action="Confirm that `gcloud` is installed and executable.",
                    command=display_command,
                )
            )

        output = CommandOutput(
            command=display_command,
            stdout=completed.stdout,
            stderr=completed.stderr,
            exit_code=completed.returncode,
        )
        if completed.returncode == 0:
            return GCPResult.success(output)

        stderr = (completed.stderr or completed.stdout).strip()
        code, action = _classify_gcloud_error(stderr)
        message = stderr or f"gcloud failed with exit code {completed.returncode}."
        return GCPResult.failure(
            GCPError(
                code=code,
                message=message,
                action=action,
                command=display_command,
                exit_code=completed.returncode,
                stderr=stderr or None,
            )
        )


def _classify_gcloud_error(stderr: str) -> tuple[str, str | None]:
    normalized = stderr.lower()
    if not normalized:
        return "command_failed", None
    if "no credentialed accounts" in normalized or "active account selected" in normalized:
        return "not_authenticated", "Run `gcloud auth login` and select the intended account."
    if "permission" in normalized or "permission_denied" in normalized or "does not have permission" in normalized:
        return "permission_denied", "Use an account with the required IAM permissions for this project."
    if (
        "not found" in normalized
        or "not_found" in normalized
        or "was not found" in normalized
        or "could not be found" in normalized
        or "cannot find service" in normalized
        or "unknown service account" in normalized
        or "httperror 404" in normalized
        or "http error 404" in normalized
        or "status=[404]" in normalized
    ):
        return "not_found", None
    if "already exists" in normalized or "already_exists" in normalized or "status=[409]" in normalized:
        return "already_exists", None
    return "command_failed", None
