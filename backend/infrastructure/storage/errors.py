from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class SessionNotFoundError(FileNotFoundError):
    pass


@dataclass(frozen=True, slots=True)
class CorruptStorageArtifactError(RuntimeError):
    path: Path
    reason: str
