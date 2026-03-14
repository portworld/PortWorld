from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Literal, TypeVar


T = TypeVar("T")

ResolvedValueSource = Literal[
    "explicit",
    "gcloud_config",
    "remembered_state",
    "default",
    "missing",
]
MutationAction = Literal[
    "created",
    "updated",
    "enabled",
    "bound",
    "existing",
    "unchanged",
]


@dataclass(frozen=True, slots=True)
class GCPError:
    code: str
    message: str
    action: str | None = None
    command: str | None = None
    exit_code: int | None = None
    stderr: str | None = None


@dataclass(frozen=True, slots=True)
class GCPResult(Generic[T]):
    ok: bool
    value: T | None = None
    error: GCPError | None = None

    @classmethod
    def success(cls, value: T) -> "GCPResult[T]":
        return cls(ok=True, value=value, error=None)

    @classmethod
    def failure(cls, error: GCPError) -> "GCPResult[T]":
        return cls(ok=False, value=None, error=error)


@dataclass(frozen=True, slots=True)
class ResolvedValue(Generic[T]):
    value: T | None
    source: ResolvedValueSource


@dataclass(frozen=True, slots=True)
class MutationOutcome(Generic[T]):
    action: MutationAction
    resource: T


@dataclass(frozen=True, slots=True)
class CommandOutput:
    command: str
    stdout: str
    stderr: str
    exit_code: int
