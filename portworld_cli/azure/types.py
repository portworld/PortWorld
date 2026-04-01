from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AzureCommandResult:
    ok: bool
    value: object | None
    message: str | None = None
