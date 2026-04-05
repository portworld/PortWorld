from __future__ import annotations

import json
import subprocess

from portworld_cli.azure.types import AzureCommandResult


class AzureExecutor:
    def run_json(self, args: list[str]) -> AzureCommandResult:
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

    def run_text(self, args: list[str]) -> AzureCommandResult:
        completed = subprocess.run(
            ["az", *args],
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
        return AzureCommandResult(ok=True, value=(completed.stdout or "").strip())
