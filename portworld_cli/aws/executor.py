from __future__ import annotations

import json
import subprocess

from portworld_cli.aws.types import AWSCommandResult


class AWSExecutor:
    def run_json(self, args: list[str]) -> AWSCommandResult:
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

    def run_text(self, args: list[str]) -> AWSCommandResult:
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
